"""
국가유산 공간정보 WMS 어댑터
---------------------------------------------------------------
국가유산청이 SHP으로 배포하지 않는 두 레이어를 WMS로 조회한다.

  https://gis-heritage.go.kr/checkKey.do?domain=..&service=WMS&request=GetMap&LAYERS=..

  TB_SHOV_MID  문화유적분포지도    ← SHP 미배포
  TB_ERHT_MID  국가유산조사구역     ← SHP 미배포

지정·등록유산과 보호구역, 현상변경 허용기준은 SHP으로 적재해
HeritageSpatialProvider가 **미터 단위 최근접 거리**로 판정한다. 이 어댑터는
그 방식으로 받을 수 없는 2종만 보완한다.

⚠️ 한계 — WMS는 이미지다
  · GetFeatureInfo는 쓸 수 없다. checkKey.do가 프록시라 QUERY_LAYERS를
    백엔드로 넘기지 않아 'MissingParameterValue'가 난다(실측 확인)
  · 따라서 **거리 대신 검토 반경 내 존재 여부와 면적**만 산출한다
  · 인증키·도메인 검증은 없다(자기 도메인·localhost·임의 도메인 모두 동일 응답)
  · 좌표계는 EPSG:5179(UTM-K)로 검토 엔진과 같아 재투영이 없다
"""
from __future__ import annotations

import io
import logging
import urllib.parse

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

DEFAULT_WMS_URL = 'https://gis-heritage.go.kr/checkKey.do'
DEFAULT_DOMAIN = 'https://gis-heritage.go.kr/'

#: ⚠️ 이 WMS는 **축척 의존 렌더링**이다(실측). 해상도를 너무 곱게 잡으면
#: 아무것도 그리지 않는다. 5.0m/px에서는 전부 흰색, 7.8m/px부터 정상 렌더링됐다.
#: 안전 여유를 두고 12m/px를 목표로 하고, 8m/px보다 곱게 요청하지 않는다.
TARGET_PIXEL_M = 12
MIN_PIXEL_M = 8
MIN_IMAGE_PX = 32
MAX_IMAGE_PX = 256

#: 배경은 순백이다. 여기에 근접한 색만 '구역 없음'으로 본다
#: (경계선·안티에일리어싱까지 배경으로 치면 구역을 놓친다)
WHITE_MIN = 250


class HeritageWmsProvider(LayerProvider):
    """국가유산 WMS 레이어 1종을 판정하는 기반 클래스"""

    category = '안전/문화재'
    data_source = '국가유산청 공간정보 WMS'
    required_settings = ()
    default_law = '문화유산의 보존 및 활용에 관한 법률'

    #: 서브클래스가 지정
    wms_layer: str = ''
    hit_status: Status = Status.CONDITIONAL
    hit_difficulty: Difficulty = Difficulty.HIGH

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 국가유산 WMS를 판정하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )
        try:
            hit_px, total_px, px_m = self._sample(q)
        except httpx.HTTPStatusError as e:
            return self.unknown(
                reason=(f'{self.item_name} 조회에 실패했습니다 '
                        f'(HTTP {e.response.status_code}). 데이터 부재가 아닙니다.'),
                action_required='잠시 후 재조회하십시오.',
            )
        except Exception as e:                                  # noqa: BLE001
            logger.exception('%s 조회 실패', self.item_name)
            return self.unknown(reason=f'{self.item_name} 조회 중 오류: {type(e).__name__}')

        if not hit_px:
            return self.item(
                status=Status.POSSIBLE,
                reason=f'검토 반경 {q.radius_m:,}m 내에서 {self.item_name}이(가) '
                       f'조회되지 않았습니다.',
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://gis-heritage.go.kr',
                raw={'layer': self.wms_layer, 'hit_pixels': 0},
            )

        area = hit_px * (px_m ** 2)
        circle_area = 3.141592653589793 * (q.radius_m ** 2)
        share = area / circle_area * 100

        return self.item(
            status=self.hit_status,
            reason=(
                f'검토 반경 {q.radius_m:,}m 내에 {self.item_name}이(가) 있습니다. '
                f'중첩 면적 약 {area:,.0f}㎡(검토 반경 면적의 {share:.1f}%). '
                f'{self.hit_note} '
                f'※ WMS 이미지({px_m:.0f}m/픽셀) 기반 판정이라 면적은 개략값이며 '
                '**경계까지의 정확한 거리는 산출되지 않습니다.**'
            ),
            difficulty=self.hit_difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://gis-heritage.go.kr',
            action_required=self.hit_action,
            raw={'layer': self.wms_layer, 'hit_pixels': hit_px,
                 'sampled_pixels': total_px, 'pixel_size_m': px_m,
                 'approx_area_m2': round(area)},
        )

    # ------------------------------------------------------------------
    def _sample(self, q: SiteQuery) -> tuple[int, int, float]:
        """검토 원 안에서 구역이 그려진 픽셀 수를 센다."""
        from PIL import Image

        site = geo.point_metric(q.lat, q.lng)
        half = float(q.radius_m)
        size = max(MIN_IMAGE_PX, min(MAX_IMAGE_PX, int(half * 2 / TARGET_PIXEL_M)))
        # 축척 하한 보장 — 너무 곱게 요청하면 서버가 아무것도 그리지 않는다
        while size > MIN_IMAGE_PX and (half * 2) / size < MIN_PIXEL_M:
            size //= 2
        px_m = (half * 2) / size

        url = getattr(settings, 'HERITAGE_WMS_URL', '') or DEFAULT_WMS_URL
        params = {
            'domain': getattr(settings, 'HERITAGE_WMS_DOMAIN', '') or DEFAULT_DOMAIN,
            'service': 'WMS',
            'version': '1.3.0',
            'request': 'GetMap',
            'LAYERS': self.wms_layer,
            'styles': 'default',
            'crs': geo.METRIC_CRS,
            'bBox': f'{site.x - half},{site.y - half},{site.x + half},{site.y + half}',
            'width': str(size),
            'height': str(size),
            'format': 'image/png',
            'transparent': 'true',
        }

        def fetch() -> bytes:
            # checkKey.do는 인증 확인 후 실제 WMS로 302 리다이렉트한다.
            # follow_redirects를 켜지 않으면 이미지 대신 302를 받는다.
            res = httpx.get(f'{url}?{urllib.parse.urlencode(params)}', timeout=45.0,
                            follow_redirects=True,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            if 'image' not in res.headers.get('Content-Type', ''):
                raise RuntimeError('이미지가 아닌 응답')
            return res.content

        blob = httpcache.get_or_set('heritage_wms', params, fetch)
        img = Image.open(io.BytesIO(blob)).convert('RGBA')
        w, h = img.size
        px = img.load()

        cx = cy = (size - 1) / 2.0
        r_px = size / 2.0
        hit = inside = 0
        for yy in range(h):
            for xx in range(w):
                if (xx - cx) ** 2 + (yy - cy) ** 2 > r_px ** 2:
                    continue
                inside += 1
                r, g, b, a = px[xx, yy]
                if a == 0:
                    continue
                # 흰 배경은 구역이 아니다
                if r >= WHITE_MIN and g >= WHITE_MIN and b >= WHITE_MIN:
                    continue
                hit += 1
        return hit, inside, px_m


# ======================================================================
class HeritageSurveyAreaProvider(HeritageWmsProvider):
    """국가유산조사구역 — 매장유산 지표조사 대상 판단의 핵심"""

    item_name = '국가유산조사구역'
    wms_layer = 'TB_ERHT_MID'
    default_article = '매장유산 보호 및 조사에 관한 법률 제6조(지표조사)'
    hit_status = Status.CONDITIONAL
    hit_difficulty = Difficulty.HIGH
    hit_note = ('국가유산조사구역은 매장유산이 확인·조사된 구역으로, '
                '개발사업 시 지표조사 및 발굴조사가 요구될 수 있습니다.')
    hit_action = ('① 관할 지자체·국가유산청에 조사구역 범위와 조사 이력을 확인 '
                  '② 매장유산 지표조사 대상 여부 판단 '
                  '③ 발굴조사가 필요한 경우 공정에 상당한 기간을 반영하십시오.')


class HeritageDistributionMapProvider(HeritageWmsProvider):
    """문화유적분포지도 — 매장유산 유존지역 판단의 법정 근거"""

    item_name = '문화유적분포지도'
    wms_layer = 'TB_SHOV_MID'
    default_article = '매장유산 보호 및 조사에 관한 법률 시행령 제3조제1항제1호'
    hit_status = Status.CONDITIONAL
    hit_difficulty = Difficulty.HIGH
    hit_note = ('시행령 제3조제1항제1호는 문화유적분포지도에 매장유산이 존재하는 것으로 '
                '표시된 지역을 **매장유산 유존지역**으로 규정합니다. 해당 시 개발행위에 '
                '앞서 매장유산 조사 절차가 요구됩니다.')
    hit_action = ('① 해당 지자체 문화유적분포지도 원본으로 유적 종류·범위 확인 '
                  '② 매장유산 유존지역 해당 여부를 국가유산청에 질의 '
                  '③ 지표조사 결과에 따라 발굴조사·보존조치 가능성을 검토하십시오.')
