"""
생태자연도 어댑터 — 국립생태원 개방 WFS
---------------------------------------------------------------
생태·자연도 1등급은 육상풍력에서 사실상 배제 사유에 해당해, 입지검토에서
가장 먼저 확인해야 할 항목이다. 종전에는 EGIS 좌표 조회 경로를 찾지 못해
항상 UNKNOWN이었다.

  https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS
    serviceKey · srs=EPSG:5186 · bbox=minx,miny,maxx,maxy · layers=tbl_opn_eczm

⚠️ 실측으로 확인한 사항 (2026-08)
  · 상류가 국립생태원 지오서버(nie-ecobank)라 **간헐적으로 다운**된다.
    이때 공공데이터포털이 HTTP_ERROR(04)를 돌려주므로 '데이터 없음'과 구분해야 한다
  · **outputFormat을 반드시 명시해야 한다.** 빼면 GML(text/xml)이 와서
    JSON 파싱이 매번 깨진다. 예전에는 기본값이 GML을 JSON화한 형태
    (featureMember[].tbl_opn_eczm.geom…)였으나 지금은 순수 GML이다.
    outputFormat=application/json 을 주면 표준 GeoJSON이 온다.
    상류가 또 바뀔 수 있어 _normalize()가 두 형태를 모두 읽는다
  · maxFeatures 미지정 시 **500건에서 잘린다**
  · 좌표계는 EPSG:5186(중부원점). 데이터가 그 좌표계라 그대로 계산한다

속성
  eczm_grad       생태자연도 등급 (1/2/3)   ← 판정 대상
  vtn_evl_grad    식생 평가등급
  amplt_evl_grad  양서파충류 평가등급
  smld_evl_grad   소형포유류 평가등급
  tpgrph_evl_grad 지형 평가등급
  plnt_cln_ttle   식물군락명 (예: 버드나무군락)
"""
from __future__ import annotations

import logging
import urllib.parse

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

DEFAULT_BASE = 'https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService'
WFS_OP = 'wfs/getEcologyzmpWFS'
LAYER = 'tbl_opn_eczm'
ECO_CRS = 'EPSG:5186'

#: 한 번에 받을 최대 피처 수. 미지정 시 500에서 잘린다.
MAX_FEATURES = 3000

#: 출력 형식. **반드시 명시해야 한다.**
#: 지정하지 않으면 이 WFS는 GML(text/xml)을 돌려주고, 그 200 응답을 JSON으로
#: 읽다가 매번 깨져 생태자연도가 영구 UNKNOWN이 됐다. 데이터가 없어서가 아니라
#: 형식이 달라서 못 읽던 것이다. 'json'도 통하지만 표준 표기를 쓴다.
#: (format=json / type=json 은 무시되고 GML이 온다 — 실측)
OUTPUT_FORMAT = 'application/json'


class EcoNatureMapProvider(LayerProvider):
    """생태자연도 (1~3등급, 1등급이 보전 우선)"""

    category = '환경'
    item_name = '생태자연도'
    data_source = '국립생태원 생태자연도 (공공데이터포털)'
    required_settings = ('ECO_API_KEY',)
    default_law = '자연환경보전법'
    default_article = '제34조(생태·자연도의 작성·활용)'

    #: 등급별 기본 판정 — DB(RegulationRule)에 규칙이 있으면 그쪽이 우선한다.
    FALLBACK = {
        1: (Status.CONDITIONAL, Difficulty.CRITICAL,
            '생태·자연도 1등급 권역은 자연환경보전법상 보전이 우선되어 개발사업 입지를 '
            '원칙적으로 지양합니다. 환경영향평가 협의에서 회피가 요구되는 경우가 많아 '
            '해당 구역을 피하는 배치 조정을 우선 검토하십시오.'),
        2: (Status.CONDITIONAL, Difficulty.HIGH,
            '생태·자연도 2등급 권역이 포함되어 환경영향평가(또는 소규모 환경영향평가) '
            '협의 과정에서 보전·저감 방안 제시가 요구됩니다.'),
        3: (Status.POSSIBLE, Difficulty.LOW,
            '생태·자연도 3등급 권역으로 개발과 보전의 조화가 가능한 지역으로 분류됩니다.'),
    }

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 생태자연도를 판정하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )

        try:
            features, truncated = self._fetch(q)
        except EcoServiceError as e:
            return self.unknown(
                reason=(f'생태자연도 조회에 실패했습니다 — {e}. '
                        '데이터 부재가 아니라 조회 자체가 되지 않은 상태입니다.'),
                action_required='국립생태원 지오서버 장애일 수 있습니다. 잠시 후 재조회하십시오.',
                why='FETCH',
            )
        except Exception as e:                                  # noqa: BLE001
            logger.exception('생태자연도 조회 실패')
            return self.unknown(reason=f'생태자연도 조회 중 오류: {type(e).__name__}',
                                why='FETCH')

        if not features:
            return self.item(
                status=Status.UNKNOWN,
                reason=('검토 반경에서 생태·자연도 권역이 조회되지 않았습니다. '
                        '미작성 구간이거나 좌표 오류일 수 있어 부재로 단정하지 않습니다.'),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                source_url='https://www.nie-ecobank.kr',
                action_required='환경공간정보서비스(egis.me.go.kr)에서 직접 확인하십시오.',
                raw={'features': 0},
                unknown_reason='NO_DATA',
            )

        stat = self._measure(q, features)
        if not stat['by_grade']:
            return self.item(
                status=Status.UNKNOWN,
                reason='검토 원과 겹치는 생태·자연도 권역이 없습니다 (조회 범위에는 존재).',
                difficulty=Difficulty.LOW,
                confidence=Confidence.LOW,
                source_url='https://www.nie-ecobank.kr',
                raw=stat,
            )

        worst = min(stat['by_grade'])
        status, difficulty, note = self._resolve(worst)

        total = sum(stat['by_grade'].values()) or 1.0
        dist = ' · '.join(f'{g}등급 {a / total * 100:.0f}%'
                          for g, a in sorted(stat['by_grade'].items()))
        worst_area = stat['by_grade'][worst]
        share = worst_area / stat['circle_area_m2'] * 100

        communities = ', '.join(stat['communities'][:5])
        comm_txt = f' 주요 식물군락 — {communities}.' if communities else ''
        trunc_txt = (' ⚠️ 조회 상한에 도달해 일부 권역이 누락되었을 수 있습니다.'
                     if truncated else '')

        return self.item(
            status=status,
            reason=(
                f'검토 반경 {q.radius_m:,}m 내 최상위 등급은 **{worst}등급**입니다. '
                f'등급 분포 — {dist}. {worst}등급 면적 약 {worst_area:,.0f}㎡'
                f'(검토 반경 면적의 {share:.1f}%). {note}{comm_txt}{trunc_txt}'
            ),
            difficulty=difficulty,
            confidence=Confidence.MEDIUM,
            source_url='https://www.nie-ecobank.kr',
            action_required=(
                '① 1·2등급 권역을 회피하는 배치 검토 '
                '② 유역(지방)환경청과 사전 환경성 협의 '
                '③ 환경영향평가 시 식생·동물상 현지조사 결과로 등급 재확인'
            ),
            raw=stat,
        )

    # ------------------------------------------------------------------
    def _fetch(self, q: SiteQuery) -> tuple[list[dict], bool]:
        base = getattr(settings, 'ECO_API_BASE', '') or DEFAULT_BASE
        site = _to_eco(q.lat, q.lng)
        r = q.radius_m
        params = {
            'serviceKey': settings.ECO_API_KEY,
            'srs': ECO_CRS,
            'bbox': f'{site[0] - r},{site[1] - r},{site[0] + r},{site[1] + r}',
            'layers': LAYER,
            'maxFeatures': str(MAX_FEATURES),
            'outputFormat': OUTPUT_FORMAT,
        }

        def call() -> dict:
            res = httpx.get(f'{base.rstrip("/")}/{WFS_OP}?{urllib.parse.urlencode(params)}',
                            timeout=90.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            payload = res.json()
            # 상류 장애 시 공공데이터포털이 이 형태로 돌려준다
            if 'OpenAPI_ServiceResponse' in payload:
                head = (payload['OpenAPI_ServiceResponse'].get('cmmMsgHeader') or {})
                raise EcoServiceError(head.get('errMsg') or '알 수 없는 오류')
            return payload

        payload = httpcache.get_or_set('econature', params, call)
        feats = _normalize(payload)
        return feats, len(feats) >= MAX_FEATURES

    # ------------------------------------------------------------------
    def _measure(self, q: SiteQuery, features: list[dict]) -> dict:
        """검토 원과 교차하는 면적을 등급별로 집계한다."""
        from shapely.geometry import Point

        site = _to_eco(q.lat, q.lng)
        circle = Point(site).buffer(q.radius_m)

        by_grade: dict[int, float] = {}
        communities: list[str] = []
        for f in features:
            grade = _int(f.get('eczm_grad'))
            if grade is None:
                continue
            poly = f.get('_shape')
            if poly is None:
                continue
            try:
                inter = poly.intersection(circle)
            except Exception:                                   # noqa: BLE001
                continue
            if inter.is_empty:
                continue
            by_grade[grade] = by_grade.get(grade, 0.0) + float(inter.area)
            name = (f.get('plnt_cln_ttle') or '').strip()
            if name and name not in communities:
                communities.append(name)

        return {
            'by_grade': {g: round(a, 1) for g, a in sorted(by_grade.items())},
            'communities': communities,
            'feature_count': len(features),
            'circle_area_m2': round(float(circle.area), 1),
            'crs': ECO_CRS,
        }

    # ------------------------------------------------------------------
    def _resolve(self, grade: int):
        from ..models import RegulationRule

        rule = RegulationRule.objects.filter(
            layer='생태자연도', condition_key=str(grade), is_active=True).first()
        if rule:
            return (Status(rule.status), Difficulty(rule.difficulty), rule.reason_template)
        return self.FALLBACK.get(
            grade, (Status.UNKNOWN, Difficulty.MEDIUM, '등급 판정 기준을 확인하지 못했습니다.'))


class EcoServiceError(RuntimeError):
    """상류 서비스 오류 — 데이터 부재와 구분한다."""


# ----------------------------------------------------------------------
def _normalize(payload: dict) -> list[dict]:
    """
    응답을 속성 dict + '_shape'(shapely 도형) 형태로 평탄화한다.

    이 서비스는 같은 URL로 두 가지 형태를 돌려준 전력이 있다.
      · GeoJSON            features[].properties / features[].geometry
      · GML을 JSON화한 것   featureMember[].tbl_opn_eczm.geom.MultiPolygon…

    지금은 outputFormat을 명시해 GeoJSON을 받지만, 상류가 또 바뀌어도
    조용히 0건이 되지 않도록 두 형태를 모두 읽는다. 0건은 '1등급 없음'으로
    읽히기 때문에 형식 문제로 비는 것이 가장 위험하다.
    """
    feats = payload.get('features')
    if isinstance(feats, list):
        out = []
        for f in feats:
            props = dict(f.get('properties') or {})
            props['_shape'] = geo.geom_from_geojson(f.get('geometry'))
            out.append(props)
        return out

    members = payload.get('featureMember') or []
    if isinstance(members, dict):
        members = [members]
    out = []
    for m in members:
        props = dict(m.get(LAYER) or {})
        props['_shape'] = _gml_to_shape(props.get('geom'))
        out.append(props)
    return out


# ----------------------------------------------------------------------
def _to_eco(lat: float, lng: float) -> tuple[float, float]:
    from pyproj import Transformer
    t = Transformer.from_crs(geo.GEOGRAPHIC_CRS, ECO_CRS, always_xy=True)
    return t.transform(lng, lat)


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _coords(node: dict) -> list[tuple[float, float]]:
    """GML coordinates 노드 → [(x, y), …]. 좌표 문자열은 빈 키('')에 들어 있다."""
    raw = (node or {}).get('') or ''
    pts = []
    for pair in str(raw).split():
        try:
            x, y = pair.split(',')[:2]
            pts.append((float(x), float(y)))
        except ValueError:
            continue
    return pts


def _polygon(node: dict):
    """GML Polygon 노드 → shapely Polygon (내부 링 포함)."""
    from shapely.geometry import Polygon

    outer = ((node.get('outerBoundaryIs') or {}).get('LinearRing') or {})
    shell = _coords(outer.get('coordinates'))
    if len(shell) < 4:
        return None

    holes = []
    inner = node.get('innerBoundaryIs')
    if inner:
        for ring in (inner if isinstance(inner, list) else [inner]):
            pts = _coords(((ring or {}).get('LinearRing') or {}).get('coordinates'))
            if len(pts) >= 4:
                holes.append(pts)
    try:
        p = Polygon(shell, holes)
        return p if p.is_valid else p.buffer(0)
    except Exception:                                           # noqa: BLE001
        return None


def _gml_to_shape(geom: dict | None):
    """GML MultiPolygon(JSON 표현) → shapely 도형"""
    from shapely.geometry import MultiPolygon

    if not geom:
        return None
    mp = geom.get('MultiPolygon') or {}
    members = mp.get('polygonMember')
    if members is None:
        poly = geom.get('Polygon')
        return _polygon(poly) if poly else None
    if isinstance(members, dict):
        members = [members]

    polys = []
    for m in members:
        p = _polygon((m or {}).get('Polygon') or {})
        if p is not None and not p.is_empty:
            polys.append(p)
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    try:
        return MultiPolygon(polys)
    except Exception:                                           # noqa: BLE001
        merged = polys[0]
        for p in polys[1:]:
            merged = merged.union(p)
        return merged
