"""
철새도래지 어댑터 — 생활안전지도 WMS
---------------------------------------------------------------
「육상태양광 발전사업 환경성 평가 협의지침」의 **입지회피지역** 가운데 하나다.
보전가치가 있는 동·식물과 철새가 서식·도래하는 곳이면 환경영향평가 협의에서
사전협의·추가조사가 요구된다.

인허가 전문업체 검토서(장흥 후보지, 2026-08)가 이 항목을 짚었다.

    보전가치가 있는 동·식물, 철새 등
    → 철새도래지 해당 (추후 협의 진행 시 추가 조사 수행 필요)
    → 영산강 유역환경청 사전협의 필요

우리 62개 항목에 이 갈래가 없어 통째로 빠져 있었다.

■ 자료 출처 — 같은 자료가 두 곳에 있다

레이어명 `A2SM_MGRBIRDSHBTT` 로 양쪽이 같은 자료임을 확인했다.

    ① SHP 내려받기 (권장)
       V-World 디지털트윈국토 > 공간정보 다운로드 > 철새도래지
       vworld.kr/dtmk/dtmk_ntads_s002.do?dsId=30160
       A2SM_MGRBIRDSHBTT.zip (POLYGON) · 국립재난안전연구원 · CC BY-NC-ND

    ② 생활안전지도 오픈API
       XML  safemap.go.kr/openapi2/IF_0099
       WMS  safemap.go.kr/openapi2/IF_0099_WMS
       인증키는 산사태위험등급(IF_0046)과 같은 키를 쓴다

**①이 낫다.** ②는 이미지라 '몇 % 겹치는가'까지지만 ①은 도형이라 도래지
이름과 거리를 보고서에 실을 수 있다. 그래서 SHP가 적재돼 있으면 그쪽을
먼저 본다.

⚠️ V-World **오픈API**(2D 데이터 158종·WMS 355종)에는 이 자료가 없다.
   전수 검색으로 확인했다. 다운로드 쪽에만 있으므로 API로 찾다 없다고
   단정하지 말 것 — 실제로 그렇게 잘못 판단했었다.

■ ⚠️ 2026-08 현재 이 인터페이스가 서버 오류를 낸다

    IF_0046_WMS (산사태)   1초 · PNG 정상
    IF_0099_WMS (철새)    60초 · APPLICATION_ERROR
    IF_0098/IF_0100(미신청)  0초 · SERVICE_KEY_IS_NOT_REGISTERED

미신청 인터페이스는 **즉시** 키 오류를 낸다. 철새도래지는 키 검증을 통과한 뒤
60초 만에 내부 오류로 끝난다 — 즉 **신청·승인은 되었고 서버 쪽이 실패**하는
것이다. srs(4326/3857/5179)·bbox 크기·layers/styles 조합을 모두 바꿔 봐도
같다. 관리부서(환경부 044-201-6482) 문의가 필요하다.

그때까지 이 항목은 **UNKNOWN(확인 필요)** 으로 남는다. 조용히 '해당 없음'으로
두면 입지회피지역을 못 본 채 통과시키게 된다 — 이 시스템이 가장 경계하는
실패다.
"""
from __future__ import annotations

import io
import logging
import urllib.parse

import httpx
from django.conf import settings
from django.core.cache import cache

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery, explain_error

logger = logging.getLogger(__name__)

WMS_URL = 'https://safemap.go.kr/openapi2/IF_0099_WMS'
#: 범례 조회(`/openapi2/lgdInfo?intId=IF_0099`)로 확인한 값. LAYER와 STYLE이 같다.
LAYER = 'A2SM_MGRBIRDSHBTT'

#: 요청 이미지 한 변의 픽셀 수. 도래지 경계는 넓은 면이라 잘게 볼 까닭이 없다.
IMAGE_PX = 256
#: 이 비율을 넘게 칠해져 있으면 '구역 대부분이 도래지'로 본다.
MOSTLY_RATIO = 0.6
#: 알파가 이 값을 넘으면 칠해진 것으로 센다(경계 흐림 방지)
ALPHA_FLOOR = 40

SOURCE_URL = 'https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?dsId=30160'
#: 적재본의 출처·이용조건. **보고서에 반드시 실어야 한다** — 라이선스가
#: CC BY-NC-ND(비영리·변경금지)라 외부 제출 시 출처 표기가 필요하고,
#: 갱신일이 원본 API보다 오래된 판일 수 있어 기준을 밝혀야 한다.
_PROVENANCE = ('※ 판정 근거는 국립재난안전연구원 「철새도래지」 공간자료'
               '(V-World 공간정보 다운로드, CC BY-NC-ND)입니다. '
               '갱신일을 확인해 최신 여부를 함께 검토하십시오.')

#: 한 번 요청에 기다릴 시간(초). 서비스가 죽어 있으면 서버가 60초를 꽉 채우고
#: 오류를 내므로, 그보다 짧게 끊어 검토가 멎어 보이지 않게 한다.
REQUEST_TIMEOUT_S = 25.0
#: 실패를 기억해 두는 시간(초). 지점마다 25초씩 다시 기다리지 않기 위한 것이다.
#: **판정을 바꾸지는 않는다** — 여전히 '확인 필요'로 남는다.
DOWN_TTL_S = 600
_DOWN_KEY = 'windsite:birds_down'


class BirdHabitatProvider(LayerProvider):
    """
    철새도래지 — 환경영향평가 입지회피지역.

    **판정은 조건부까지만 간다.** 도래지에 든다는 사실이 곧 불가가 아니라,
    유역환경청 사전협의와 추가 조사가 필요하다는 뜻이기 때문이다. 실제로
    도래지 안에서 허가된 사업이 있다.
    """

    category = '환경'
    item_name = '철새도래지'
    data_source = '환경부 철새도래지 (생활안전지도 WMS · 국립환경과학원 겨울철새 센서스)'
    required_settings = ('FOREST_API_KEY',)
    default_law = '환경영향평가법'
    default_article = '제22조 · 육상태양광 환경성 평가 협의지침'

    #: SHP를 적재해 두면 **그쪽을 먼저 쓴다.** 생활안전지도 API가 응답하지
    #: 않는 동안 자료를 손에 넣을 수 있는 길이고, 이미지가 아니라 도형이라
    #: 도래지 이름과 거리까지 낼 수 있어 더 낫다.
    #:
    #:     python manage.py load_spatial_shp \
    #:         --zip data/birds/A2SM_MGRBIRDSHBTT.zip \
    #:         --category 환경 --source 국립재난안전연구원
    #:
    #: 데이터셋 키는 SHP 파일명에서 만들어진다(`--prefix`를 주면 앞에 붙는다).
    #: 원본 파일명이 레이어명과 같으므로 **꼬리로 알아본다** — 접두사를 무엇으로
    #: 주든 걸리게 하기 위한 것이다.
    DATASET_SUFFIX = 'A2SM_MGRBIRDSHBTT'
    DATASET_CODE = 'BIRD_HABITAT'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        loaded = self._from_shp(q)
        if loaded is not None:
            return loaded
        if not getattr(settings, 'FOREST_API_KEY', ''):
            return self.unknown(
                reason='생활안전지도 인증키가 없어 철새도래지를 조회하지 못했습니다.',
                action_required='FOREST_API_KEY를 설정하십시오 (safemap.go.kr).')
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 철새도래지를 조회하지 못했습니다.')

        try:
            hit_px, total_px = self._sample(q)
        except Exception as e:                                  # noqa: BLE001
            logger.warning('철새도래지 조회 실패: %s', e)
            return self.unknown(
                reason=(
                    f'철새도래지 자료를 받지 못했습니다 — {explain_error(e)}. '
                    '생활안전지도 IF_0099 인터페이스가 응답하지 않는 상태입니다 — '
                    '같은 키로 산사태위험지도(IF_0046)는 정상 조회됩니다. '
                    '**입지회피지역에 해당하는지 확인하지 못했으므로 해당 없음으로 '
                    '읽지 마십시오.**'),
                action_required=(
                    '환경영향평가정보지원시스템(eiass.go.kr) 또는 관할 유역환경청에 '
                    '철새도래지 해당 여부를 직접 확인하십시오. 생활안전지도 오류는 '
                    '환경부 044-201-6482로 문의할 수 있습니다.'),
                why='FETCH')

        if not total_px:
            return self.unknown(
                reason='검토 범위에서 철새도래지 자료를 판별하지 못했습니다.')

        ratio = hit_px / total_px
        if not hit_px:
            return self.item(
                status=Status.POSSIBLE,
                reason='검토 범위가 철새도래지 경계에 들지 않습니다. '
                       '「육상태양광 환경성 평가 협의지침」의 입지회피지역 가운데 '
                       '철새 항목에는 해당하지 않습니다.',
                difficulty=Difficulty.LOW, confidence=Confidence.MEDIUM,
                source_url='https://www.safemap.go.kr',
                raw={'hit_ratio': 0.0, 'sampled_px': total_px})

        scope = ('검토 범위 **대부분**' if ratio >= MOSTLY_RATIO
                 else f'검토 범위의 약 {ratio * 100:.0f}%')
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'{scope}가 **철새도래지 경계 안**에 있습니다(국립환경과학원 겨울철새 '
                f'센서스 조사지역). 「육상태양광 발전사업 환경성 평가 협의지침」이 '
                f'보전가치가 있는 동·식물·철새 서식지를 **입지회피지역**으로 두므로, '
                f'환경영향평가 협의에서 관할 유역환경청 사전협의와 **추가 조사**가 '
                f'요구됩니다. 도래지에 든다는 사실이 곧 불가는 아니며, 조류 충돌·'
                f'서식 단절 저감대책과 조사 결과에 따라 협의가 갈립니다.'),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.MEDIUM,
            source_url='https://www.safemap.go.kr',
            action_required=(
                '관할 유역환경청에 사전협의를 요청하고, 겨울철새 조사(최소 1개 '
                '동절기)를 계획에 반영하십시오. 조류 유입 방지대책(조류 신체에 '
                '직접 위해를 주지 않는 방식)과 울타리 하단 개방(15cm 이상), 경관 '
                '식재 등 저감방안을 함께 준비하는 것이 협의에 유리합니다.'),
            raw={'hit_ratio': round(ratio, 3), 'sampled_px': total_px,
                 'layer': LAYER})

    # ------------------------------------------------------------------
    def _from_shp(self, q: SiteQuery) -> AnalysisItem | None:
        """
        적재된 SHP로 판정한다. 없으면 None을 돌려 WMS 경로로 넘긴다.

        WMS는 이미지라 '몇 % 겹치는가'까지만 알 수 있지만, SHP는 **도래지
        이름과 거리**를 낼 수 있어 협의 준비에 훨씬 쓸모 있다. 그래서 둘 다
        있으면 SHP가 이긴다.
        """
        try:
            from django.db.models import Q

            from ..models import SpatialFeature
            from shapely import wkb as shapely_wkb
        except Exception:                                       # noqa: BLE001
            return None

        loaded = (Q(dataset__code__iendswith=self.DATASET_SUFFIX)
                  | Q(dataset__code=self.DATASET_CODE))
        base = SpatialFeature.objects.filter(loaded, dataset__is_active=True)
        if not base.exists():
            return None                       # 적재 전 — WMS로 넘긴다

        site = geo.point_metric(q.lat, q.lng)
        r = float(q.radius_m)
        qs = base.filter(min_x__lte=site.x + r, max_x__gte=site.x - r,
                         min_y__lte=site.y + r, max_y__gte=site.y - r)

        hits = []
        for f in qs[:2000]:
            try:
                g = shapely_wkb.loads(bytes(f.geom_wkb))
            except Exception:                                   # noqa: BLE001
                continue
            d = site.distance(g)
            if d <= r:
                hits.append((round(d, 1), (f.name or '이름 미상')))
        if not hits:
            return self.item(
                status=Status.POSSIBLE,
                reason='검토 범위가 철새도래지 경계에 들지 않습니다(적재 자료 기준). '
                       '「육상태양광 환경성 평가 협의지침」의 입지회피지역 가운데 '
                       '철새 항목에는 해당하지 않습니다. ' + _PROVENANCE,
                difficulty=Difficulty.LOW, confidence=Confidence.HIGH,
                source_url=SOURCE_URL,
                raw={'source': 'SHP', 'hits': 0})

        hits.sort()
        names = ' · '.join(dict.fromkeys(n for _, n in hits))[:200]
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'검토 범위가 **철새도래지 경계 안**에 있습니다 — {names}. '
                f'「육상태양광 발전사업 환경성 평가 협의지침」이 보전가치가 있는 '
                f'동·식물·철새 서식지를 **입지회피지역**으로 두므로, 환경영향평가 '
                f'협의에서 관할 유역환경청 사전협의와 **추가 조사**가 요구됩니다. '
                f'도래지에 든다는 사실이 곧 불가는 아니며, 조류 충돌·서식 단절 '
                f'저감대책과 조사 결과에 따라 협의가 갈립니다.'),
            difficulty=Difficulty.HIGH, confidence=Confidence.HIGH,
            action_required=(
                '관할 유역환경청에 사전협의를 요청하고, 겨울철새 조사(최소 1개 '
                '동절기)를 계획에 반영하십시오. 조류 유입 방지대책(조류 신체에 '
                '직접 위해를 주지 않는 방식)과 울타리 하단 개방(15cm 이상), 경관 '
                '식재 등 저감방안을 함께 준비하는 것이 협의에 유리합니다. '
                + _PROVENANCE),
            source_url=SOURCE_URL,
            raw={'source': 'SHP', 'hits': len(hits), 'names': names})

    # ------------------------------------------------------------------
    def _sample(self, q: SiteQuery) -> tuple[int, int]:
        """
        검토 원 안에서 도래지로 칠해진 픽셀 수를 센다. → (칠해진 수, 전체 수)

        WMS가 이미지만 주므로 면적 비율은 픽셀로 잰다. 원 밖 모서리를 세면
        범위가 과대 반영되므로 원 안만 센다(산사태 어댑터와 같은 방식).
        """
        from PIL import Image

        site = geo.point_metric(q.lat, q.lng)
        half = float(q.radius_m)
        params = {
            'serviceKey': settings.FOREST_API_KEY,
            'service': 'WMS', 'request': 'GetMap', 'version': '1.1.1',
            'layers': LAYER, 'styles': LAYER,
            'srs': geo.METRIC_CRS,
            'bbox': f'{site.x - half},{site.y - half},{site.x + half},{site.y + half}',
            'width': str(IMAGE_PX), 'height': str(IMAGE_PX),
            'format': 'image/png', 'transparent': 'TRUE',
        }

        # 서비스가 죽어 있으면 매번 60초를 기다리게 된다. 검토 한 번에 지점이
        # 여럿이면 그만큼 곱해져 화면이 멎은 것처럼 보인다. 한 번 실패하면
        # 잠시 쉬었다 다시 본다 — **판정을 '해당 없음'으로 바꾸지는 않는다.**
        if cache.get(_DOWN_KEY):
            raise RuntimeError('직전 조회가 실패해 잠시 건너뜁니다 '
                               '(생활안전지도 IF_0099 응답 없음)')

        def fetch() -> bytes:
            res = httpx.get(f'{WMS_URL}?{urllib.parse.urlencode(params)}',
                            timeout=REQUEST_TIMEOUT_S, follow_redirects=True,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            if res.content[:4] != b'\x89PNG':
                # 오류도 200으로 오는 경우가 있어 내용을 확인한다. 확인하지
                # 않으면 오류 JSON이 '해당 없음'으로 조용히 둔갑한다.
                raise RuntimeError(
                    f'이미지가 아닌 응답: {res.headers.get("content-type")} '
                    f'{res.text[:120]}')
            return res.content

        try:
            blob = httpcache.get_or_set('birds', params, fetch)
        except Exception:
            try:
                cache.set(_DOWN_KEY, '1', DOWN_TTL_S)
            except Exception:                               # noqa: BLE001
                pass
            raise
        img = Image.open(io.BytesIO(blob)).convert('RGBA')
        px = img.load()
        w, h = img.size
        cx = cy = (IMAGE_PX - 1) / 2.0
        r_px = IMAGE_PX / 2.0

        hit = inside = 0
        for yy in range(h):
            for xx in range(w):
                if (xx - cx) ** 2 + (yy - cy) ** 2 > r_px ** 2:
                    continue
                inside += 1
                if px[xx, yy][3] >= ALPHA_FLOOR:
                    hit += 1
        return hit, inside


def habitat_geoms(q: SiteQuery, limit: int = 400) -> list:
    """
    보고서 지도용 — 검토 범위와 겹치는 철새도래지 도형(EPSG:5179).

    적재된 SHP에서만 낸다. WMS 폴백 경로는 픽셀 표본이라 도형이 없다 —
    그때는 빈 목록을 돌려주고, 지도 대신 판정 타일이 나간다.
    """
    try:
        from django.db.models import Q
        from shapely import wkb as shapely_wkb

        from ..models import SpatialFeature
    except Exception:                                           # noqa: BLE001
        return []

    p = BirdHabitatProvider()
    loaded = (Q(dataset__code__iendswith=p.DATASET_SUFFIX)
              | Q(dataset__code=p.DATASET_CODE))
    base = SpatialFeature.objects.filter(loaded, dataset__is_active=True)
    if not base.exists():
        return []

    target = q.geom
    minx, miny, maxx, maxy = target.bounds
    qs = base.filter(min_x__lte=maxx, max_x__gte=minx,
                     min_y__lte=maxy, max_y__gte=miny)[:limit]
    out = []
    for f in qs:
        try:
            g = shapely_wkb.loads(bytes(f.geom_wkb))
        except Exception:                                       # noqa: BLE001
            continue
        if g is not None and not g.is_empty and g.intersects(target):
            out.append(g)
    return out
