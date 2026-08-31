"""
생태·경관보전지역 어댑터 (전용 WFS)
---------------------------------------------------------------
자연환경보전법 제12조·제23조에 따라 환경부장관 또는 시·도지사가 지정하는 구역이다.
핵심구역에서는 개발행위가 원칙적으로 금지되어 풍력 입지에 결정적이다.

  https://apis.data.go.kr/1192000/apVhdService_EcgyScenePresvArea
    /getOpnEcgyScenePresvAreaWFS?serviceKey=..&bbox=..&maxFeatures=..

필지 지역지구(getLandUseAttr)로도 '걸려 있는지'는 알 수 있지만, 그것은
조회한 필지 6개에 한정되고 거리도 나오지 않는다. 이 어댑터는 검토 반경 전체에서
**최근접 거리(m)** 를 산출한다. 필지에 걸리지 않아도 인근에 있으면 환경영향평가에서
쟁점이 되므로 거리 정보가 실무상 중요하다.

⚠️ 실측 확인 사항
  · 좌표계는 **EPSG:5179**로 검토 엔진과 같다 → 재투영 없이 거리 계산
  · 응답은 GML 3.2 (gml:MultiSurface / gml:posList, 좌표가 공백 구분 x y x y …)
  · maxFeatures 최대 100. 전국 조회 시 33개 구역이라 검토 반경에서는 충분하다
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery, explain_error

logger = logging.getLogger(__name__)

DEFAULT_BASE = 'https://apis.data.go.kr/1192000/apVhdService_EcgyScenePresvArea'
WFS_OP = 'getOpnEcgyScenePresvAreaWFS'
ECO_AREA_CRS = 'EPSG:5179'

#: 응답 상한 (문서상 최대 100)
MAX_FEATURES = 100
#: 검토 반경에 더해 탐색할 여유 — 인근 구역까지 거리로 보고하기 위함
SEARCH_MARGIN_M = 5000


class EcoAreaProvider(LayerProvider):
    """생태·경관보전지역 (최근접 거리 판정)"""

    category = '환경'
    item_name = '생태·경관보전지역'
    data_source = '국립생태원 생태경관보전지역 (공공데이터포털)'
    required_settings = ('ECO_API_KEY',)
    default_law = '자연환경보전법'
    default_article = '제15조(생태·경관보전지역에서의 행위제한)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 생태·경관보전지역을 판정하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )
        try:
            areas = self._fetch(q)
        except Exception as e:                                  # noqa: BLE001
            logger.exception('생태·경관보전지역 조회 실패')
            return self.unknown(
                reason=f'생태·경관보전지역 조회에 실패했습니다 — {explain_error(e)}. '
                       '데이터 부재가 아니라 조회 실패입니다.',
                action_required='잠시 후 재조회하십시오.',
                why='FETCH',
            )

        if not areas:
            return self.item(
                status=Status.POSSIBLE,
                reason=(f'{q.scope_label} 및 주변 '
                        f'{SEARCH_MARGIN_M / 1000:.0f}km 내에서 생태·경관보전지역이 '
                        '조회되지 않았습니다.'),
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                source_url='https://www.nie-ecobank.kr',
                raw={'areas': []},
            )

        nearest = areas[0]
        inside = [a for a in areas if a['distance_m'] == 0]
        within = [a for a in areas if a['distance_m'] <= q.radius_m]

        names = ', '.join(f'{a["name"]}({geo.format_distance(a["distance_m"])})'
                          for a in areas[:4])

        if inside:
            return self.item(
                status=Status.CONDITIONAL,
                reason=(
                    f'대상 지점이 생태·경관보전지역과 중첩됩니다 — '
                    f'{", ".join(a["name"] for a in inside)}. '
                    '핵심구역에서는 건축물 신축 등 개발행위가 원칙적으로 금지되며, '
                    '완충·전이구역도 행위제한을 받습니다.'
                ),
                difficulty=Difficulty.CRITICAL,
                confidence=Confidence.MEDIUM,
                source_url='https://www.nie-ecobank.kr',
                action_required=(
                    '① 구역 구분(핵심·완충·전이)을 확인하십시오 '
                    '② 핵심구역이면 입지 변경을 우선 검토하십시오 '
                    '③ 유역(지방)환경청과 사전 협의가 필요합니다'
                ),
                raw={'areas': areas, 'inside': [a['name'] for a in inside]},
            )

        if within:
            return self.item(
                status=Status.CONDITIONAL,
                reason=(
                    f'{q.scope_label} 내에 생태·경관보전지역이 있습니다 — {names}. '
                    '중첩되지는 않으나 인접 개발은 환경영향평가에서 '
                    '경관·생태 연결성 훼손 여부가 중점 검토됩니다.'
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.MEDIUM,
                source_url='https://www.nie-ecobank.kr',
                action_required='유역(지방)환경청과 사전 환경성 협의를 진행하십시오.',
                raw={'areas': areas},
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=(f'{q.scope_label} 내에는 생태·경관보전지역이 없습니다. '
                    f'가장 가까운 구역은 {nearest["name"]}로 '
                    f'{geo.format_distance(nearest["distance_m"])} 떨어져 있습니다.'),
            difficulty=Difficulty.LOW,
            confidence=Confidence.MEDIUM,
            source_url='https://www.nie-ecobank.kr',
            raw={'areas': areas},
        )

    # ------------------------------------------------------------------
    def _fetch(self, q: SiteQuery) -> list[dict]:
        """검토 반경 + 여유 범위의 구역을 받아 최근접 거리순으로 돌려준다."""
        base = getattr(settings, 'ECO_AREA_API_BASE', '') or DEFAULT_BASE
        site = geo.point_metric(q.lat, q.lng)     # EPSG:5179 — 응답 좌표계와 동일
        r = q.radius_m + SEARCH_MARGIN_M
        params = {
            'serviceKey': settings.ECO_API_KEY,
            'bbox': f'{site.x - r},{site.y - r},{site.x + r},{site.y + r}',
            'maxFeatures': str(MAX_FEATURES),
        }

        def call() -> str:
            res = httpx.get(f'{base.rstrip("/")}/{WFS_OP}?{urllib.parse.urlencode(params)}',
                            timeout=60.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            return res.text

        body = httpcache.get_or_set('ecoarea', params, call)
        if 'OpenAPI_ServiceResponse' in body:
            msg = re.search(r'<errMsg>([^<]+)</errMsg>', body)
            raise RuntimeError(msg.group(1) if msg else '서비스 오류')

        out: list[dict] = []
        for name, poly in _parse_gml(body):
            if poly is None:
                continue
            try:
                d = site.distance(poly)
            except Exception:                                   # noqa: BLE001
                continue
            out.append({'name': name or '(명칭 없음)', 'distance_m': round(d, 1)})
        out.sort(key=lambda a: a['distance_m'])
        return out


# ----------------------------------------------------------------------
def _parse_gml(xml_text: str):
    """GML 3.2 → [(지역명, shapely 도형)]"""
    from shapely.geometry import MultiPolygon, Polygon

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning('생태경관보전지역 GML 파싱 실패')
        return

    def local(tag: str) -> str:
        return tag.rsplit('}', 1)[-1]

    for member in root.iter():
        if local(member.tag) != 'opn_ecgy_scene_presv_area_a':
            continue
        name = ''
        polys = []
        for child in member.iter():
            lt = local(child.tag)
            if lt == 'area_nm' and child.text:
                name = child.text.strip()
            elif lt == 'posList' and child.text:
                nums = [float(v) for v in child.text.split() if v]
                pts = list(zip(nums[0::2], nums[1::2]))
                if len(pts) >= 4:
                    try:
                        p = Polygon(pts)
                        polys.append(p if p.is_valid else p.buffer(0))
                    except Exception:                           # noqa: BLE001
                        continue
        if not polys:
            yield name, None
            continue
        if len(polys) == 1:
            yield name, polys[0]
        else:
            try:
                yield name, MultiPolygon([p for p in polys if p.geom_type == 'Polygon'])
            except Exception:                                   # noqa: BLE001
                merged = polys[0]
                for p in polys[1:]:
                    merged = merged.union(p)
                yield name, merged
