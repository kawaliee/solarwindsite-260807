"""
브이월드(V-World) 기반 어댑터
---------------------------------------------------------------
V-World 데이터 API 2.0 (GetFeature) 사용.
  https://api.vworld.kr/req/data?service=data&request=GetFeature&data=<레이어>&...

인증키 발급: https://www.vworld.kr  →  오픈API 인증키 신청 (사용 도메인 등록 필요)
  ※ 로컬 개발 시 'localhost' 도메인을 반드시 등록해야 호출이 허용됩니다.

설계
  1) **레이어 ID·속성명을 코드에 두지 않는다.** RegulationLayer(DB)에서 읽는다.
     그 값은 scripts/vworld_probe.py의 서버 실측 결과에서 시드된다.
     → docs/WINDSITE_VWORLD_LAYERS.md
  2) `geometry=true`로 도형을 함께 받아 **최근접 거리(m)** 를 산출한다.
     교차 여부만 보던 기존 방식으로는 "경계에서 90m 떨어져 있음" 같은
     실무 표현이 불가능했다.
  3) 세부 구역명별 판정은 RegulationRule(DB)이 레이어 기본값을 덮어쓴다.
"""
from __future__ import annotations

import logging
import re

from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

VWORLD_DATA_URL = 'https://api.vworld.kr/req/data'

#: 난이도 서열 — 최악값 선택에 사용
_DIFF_ORDER = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

#: 발전기 최고높이(블레이드 끝) 기본값(m). 공역 하한고도와 비교하는 데 쓴다.
#: 국내 육상풍력 주력 기종(3~5.5MW급)의 통상 범위를 넘지 않는 보수적 값이며,
#: 기종이 확정되면 settings.WINDSITE_TURBINE_TIP_HEIGHT_M 으로 덮어쓴다.
DEFAULT_TIP_HEIGHT_M = 200

#: 항공 고도 표기 파서 — 'FL 400' / '10 000 AMSL' / '3000 FT AGL' / 'GND' / 'UNL'
_ALT_FL = re.compile(r'FL\s*(\d+)', re.I)
_ALT_FT = re.compile(r'(\d[\d\s,]*)\s*(?:FT|AMSL|AGL|MSL)', re.I)
_FT_PER_M = 3.28084


def parse_altitude_ft(raw: str | None) -> float | None:
    """
    공역 고도 표기를 피트로 환산한다. 판별 불가 시 None(→ 고도 비교를 하지 않음).

      'GND' / 'SFC' → 0        지표면
      'UNL'         → None     제한 없음(상한 표기라 하한 판단에 쓰지 않음)
      'FL 400'      → 40,000ft
      '10 000 AMSL' → 10,000ft
    """
    s = (raw or '').strip().upper()
    if not s:
        return None
    if s in ('GND', 'SFC', 'SURFACE'):
        return 0.0
    if s.startswith('UNL'):
        return None
    m = _ALT_FL.search(s)
    if m:
        return float(m.group(1)) * 100
    m = _ALT_FT.search(s)
    if m:
        try:
            return float(m.group(1).replace(' ', '').replace(',', ''))
        except ValueError:
            return None
    # 단위 없이 숫자만 있는 경우도 피트 표기로 본다 (항공 공역 관례)
    digits = re.fullmatch(r'[\d\s,]+', s)
    if digits:
        try:
            return float(s.replace(' ', '').replace(',', ''))
        except ValueError:
            return None
    return None


class VworldClient:
    """데이터 API 호출 + 응답 정규화 (레이어 무관)"""

    #: 데이터 API 1회 응답 상한
    MAX_SIZE = 1000

    @staticmethod
    def fetch(layer_id: str, lat: float, lng: float, radius_m: int,
              size: int = 300, geometry: bool = True, page: int = 1) -> dict:
        params = {
            'service': 'data',
            'request': 'GetFeature',
            'data': layer_id,
            'key': settings.VWORLD_API_KEY,
            'domain': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
            'geomFilter': f'POINT({lng} {lat})',
            'buffer': str(int(radius_m)),
            'size': str(size),
            'page': str(page),
            'format': 'json',
            'crs': 'EPSG:4326',
            'geometry': 'true' if geometry else 'false',
        }
        def call() -> dict:
            res = LayerProvider.get(VWORLD_DATA_URL, params, timeout=30.0)
            res.raise_for_status()
            return res.json()

        return httpcache.get_or_set('vworld', params, call)

    @classmethod
    def fetch_all(cls, layer_id: str, lat: float, lng: float, radius_m: int,
                  max_pages: int = 10, geometry: bool = True) -> tuple[list[dict], dict]:
        """
        페이지를 끝까지 넘겨 피처를 모두 수집한다.

        1회 상한(1,000건)만 쓰면 필지가 많은 지점에서 **말없이 잘려** 면적이
        과소 산출된다. 상한에 도달해 중단한 경우 meta['truncated']=True로 알린다.
        """
        collected: list[dict] = []
        first: dict = {}
        total_pages = 1
        page = 1
        while page <= max_pages:
            payload = cls.fetch(layer_id, lat, lng, radius_m,
                                size=cls.MAX_SIZE, geometry=geometry, page=page)
            if page == 1:
                first = payload
                if cls.status_of(payload) == 'ERROR':
                    return [], {'payload': payload, 'truncated': False, 'pages': 0}
                total_pages = cls.total_pages(payload)
            feats = cls.features(payload)
            collected.extend(feats)
            if len(feats) < cls.MAX_SIZE or page >= total_pages:
                break
            page += 1

        return collected, {
            'payload': first,
            'truncated': total_pages > max_pages,
            'pages': page,
            'total_pages': total_pages,
        }

    @staticmethod
    def total_pages(payload: dict) -> int:
        try:
            return max(1, int(payload['response']['page']['total']))
        except (KeyError, TypeError, ValueError):
            return 1

    @staticmethod
    def status_of(payload: dict) -> str:
        try:
            return payload['response']['status']
        except (KeyError, TypeError):
            return 'UNKNOWN'

    @staticmethod
    def error_text(payload: dict) -> str:
        try:
            return payload['response']['error']['text'] or ''
        except (KeyError, TypeError):
            return ''

    @staticmethod
    def features(payload: dict) -> list[dict]:
        try:
            return payload['response']['result']['featureCollection']['features']
        except (KeyError, TypeError):
            return []


class VworldLayerProvider(LayerProvider):
    """
    RegulationLayer 한 건을 조회·판정하는 범용 어댑터.

    엔진은 활성 레이어 수만큼 이 어댑터를 생성해 실행한다.
    """

    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 데이터 API'

    def __init__(self, layer):
        self.layer = layer
        self.category = layer.category or '규제/법령'
        self.item_name = layer.title or layer.code
        self.default_law = layer.law
        self.default_article = layer.article

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        lyr = self.layer

        # 명칭 속성이 아예 없는 레이어(NO_NAME)는 구역명 없이 저촉 여부만 판정한다.
        # 그 외에 속성을 확정하지 못한 경우(UNRESOLVED/ERROR)는 추측하지 않고 보류한다.
        if not lyr.name_field and lyr.probe_status != 'NO_NAME':
            return self.unknown(
                reason=(
                    f'[{lyr.code}] 레이어의 명칭 속성을 확정하지 못해 판정을 보류합니다. '
                    f'(실측 상태: {lyr.probe_status or "미실측"})'
                ),
                action_required='`python scripts/vworld_probe.py` 재실행 후 '
                                '`seed_windsite_layers`로 속성명을 갱신하십시오.',
            )

        # 조회 반경 = 검토 반경 + 레이어 고유 보호범위 + 근접 판정 임계
        radius = q.radius_m + lyr.search_margin_m + lyr.proximity_m
        payload = VworldClient.fetch(lyr.layer_id, q.lat, q.lng, radius)
        status = VworldClient.status_of(payload)

        if status == 'ERROR':
            return self.unknown(
                reason=(
                    f'V-World 응답이 ERROR입니다 — {VworldClient.error_text(payload)[:150]} '
                    f'(레이어 {lyr.layer_id})'
                ),
                action_required='인증키 상태와 사용 도메인(localhost) 등록을 확인하십시오.',
            )

        feats = VworldClient.features(payload)
        if not feats:
            # 조회는 성공했으나 반경 내 피처가 없음 = 저촉 없음
            return self._clear(q, radius)

        return self._judge(q, feats)

    # ------------------------------------------------------------------
    def _clear(self, q: SiteQuery, radius: int) -> AnalysisItem:
        lyr = self.layer
        margin = ''
        if lyr.search_margin_m:
            margin = f'(보호범위 {lyr.search_margin_m:,}m 가산 포함) '
        return self.item(
            status=Status.POSSIBLE,
            reason=(
                f'대상 지점 반경 {radius:,}m {margin}내에서 {self.item_name} 구역이 '
                f'조회되지 않았습니다.'
            ),
            difficulty=Difficulty.LOW,
            confidence=Confidence.MEDIUM,
            source_url=lyr.source_url or 'https://www.vworld.kr',
            action_required='',
            raw={'code': lyr.code, 'layer': lyr.layer_id,
                 'feature_count': 0, 'search_radius_m': radius},
        )

    # ------------------------------------------------------------------
    def _judge(self, q: SiteQuery, feats: list[dict]) -> AnalysisItem:
        """피처별 최근접 거리를 산출하고 가장 불리한 건으로 판정한다."""
        lyr = self.layer
        site = None
        geo_error = ''
        try:
            site = geo.point_metric(q.lat, q.lng)
        except geo.GeoUnavailable as e:
            geo_error = str(e)

        hits: list[dict] = []
        for f in feats:
            props = f.get('properties') or {}
            name = (props.get(lyr.name_field) or '').strip() if lyr.name_field else ''
            rec: dict = {'name': name or self.item_name, 'distance_m': None}
            for k in (lyr.extra_fields or [])[:6]:
                if props.get(k):
                    rec[k] = props[k]
            if lyr.altitude_floor_field:
                rec['altitude_floor_ft'] = parse_altitude_ft(
                    props.get(lyr.altitude_floor_field))

            if site is not None:
                g = geo.geom_from_geojson(f.get('geometry'))
                if g is not None:
                    try:
                        rec['distance_m'] = round(geo.distance_m(site, geo.to_metric(g)), 1)
                    except Exception:                            # noqa: BLE001
                        logger.debug('거리 산출 실패 layer=%s', lyr.layer_id, exc_info=True)
            hits.append(rec)

        # 근접 임계가 설정된 레이어는 임계 밖 피처를 저촉으로 보지 않는다
        measured = [h for h in hits if h['distance_m'] is not None]
        if lyr.proximity_m and measured:
            within = [h for h in measured if h['distance_m'] <= lyr.proximity_m]
            if not within:
                nearest = min(measured, key=lambda h: h['distance_m'])
                return self.item(
                    status=Status.POSSIBLE,
                    reason=(
                        f'가장 가까운 {self.item_name}은(는) {nearest["name"]}로 '
                        f'{geo.format_distance(nearest["distance_m"])} 떨어져 있어 '
                        f'근접 판정 기준({lyr.proximity_m:,}m)을 벗어납니다.'
                    ),
                    difficulty=Difficulty.LOW,
                    confidence=Confidence.MEDIUM,
                    source_url=lyr.source_url or 'https://www.vworld.kr',
                    raw={'code': lyr.code, 'layer': lyr.layer_id,
                         'nearest': nearest, 'feature_count': len(hits)},
                )
            hits = within

        # 공역 하한고도 판정 — 발전기 최고높이보다 높은 곳에만 적용되는 공역은
        # 평면이 겹쳐도 저촉이 아니다. (평면 중첩만 보면 전국 대부분이 오탐이 된다)
        if self.layer.altitude_floor_field:
            cleared = self._altitude_clearance(hits)
            if cleared is not None:
                return cleared

        # 구역명별 세부 규칙 (DB) — 없으면 레이어 기본값
        status, difficulty, law, article, confidence, rule_reason = self._resolve_rule(
            [h['name'] for h in hits])

        overlapping = [h for h in hits if h['distance_m'] == 0]
        nearest = min((h for h in hits if h['distance_m'] is not None),
                      key=lambda h: h['distance_m'], default=None)

        names = ', '.join(dict.fromkeys(h['name'] for h in hits))[:200]
        if overlapping:
            head = f'대상 지점이 {self.item_name} 구역과 중첩됩니다 — {names}.'
        elif nearest:
            head = (f'{self.item_name} 경계로부터 '
                    f'{geo.format_distance(nearest["distance_m"])} 지점입니다 — {names}.')
        else:
            head = f'검토 반경 내 {self.item_name}이(가) 조회되었습니다 — {names}.'
            if geo_error:
                head += ' (거리 산출 불가 — 공간연산 라이브러리 미설치)'

        reason = f'{head} {rule_reason}'.strip()
        return self.item(
            status=status,
            reason=reason,
            difficulty=difficulty,
            law=law,
            article=article,
            confidence=confidence,
            source_url=lyr.source_url or 'https://www.vworld.kr',
            action_required=lyr.action_required,
            raw={
                'code': lyr.code,
                'layer': lyr.layer_id,
                'feature_count': len(hits),
                'overlapping': len(overlapping),
                'nearest': nearest,
                'features': hits[:20],
            },
        )

    # ------------------------------------------------------------------
    def _altitude_clearance(self, hits: list[dict]) -> AnalysisItem | None:
        """
        모든 검출 공역의 하한고도가 발전기 최고높이보다 높으면 '저촉 없음'으로 본다.
        하나라도 하한을 판별하지 못하거나 낮으면 None을 돌려 정상 판정으로 넘긴다.
        """
        tip_m = float(getattr(settings, 'WINDSITE_TURBINE_TIP_HEIGHT_M',
                              DEFAULT_TIP_HEIGHT_M))
        tip_ft = tip_m * _FT_PER_M

        floors = [h.get('altitude_floor_ft') for h in hits]
        if not floors or any(f is None for f in floors):
            return None
        if min(floors) <= tip_ft:
            return None

        lowest = min(floors)
        names = ', '.join(dict.fromkeys(h['name'] for h in hits))[:120]
        return self.item(
            status=Status.POSSIBLE,
            reason=(
                f'{self.item_name}({names})과 평면상 겹치지만, 해당 공역의 하한고도는 '
                f'{lowest:,.0f}ft({lowest / _FT_PER_M:,.0f}m)로 발전기 최고높이 '
                f'{tip_m:,.0f}m보다 높아 저촉되지 않습니다.'
            ),
            difficulty=Difficulty.LOW,
            confidence=Confidence.MEDIUM,
            source_url=self.layer.source_url or 'https://www.vworld.kr',
            action_required=(
                '기종 확정 후 최고높이가 달라지면 재검토하십시오. 공역 하한고도와 무관하게 '
                '관할부대·국토교통부 협의 대상일 수 있습니다.'
            ),
            raw={'code': self.layer.code, 'layer': self.layer.layer_id,
                 'altitude_cleared': True, 'lowest_floor_ft': lowest,
                 'tip_height_m': tip_m, 'features': hits[:10]},
        )

    # ------------------------------------------------------------------
    def _resolve_rule(self, names: list[str]):
        """
        RegulationRule(DB)에서 구역명에 해당하는 규칙을 찾아 가장 불리한 것을 채택.
        규칙이 없으면 레이어 기본값을 쓴다. (판정 기준을 코드에 두지 않기 위함)
        """
        from ..models import RegulationRule                     # 지연 import

        lyr = self.layer
        rules = list(RegulationRule.objects.filter(layer=lyr.code, is_active=True))
        matched = [r for r in rules
                   if any(r.condition_key and r.condition_key in n for n in names)]

        if not matched:
            return (Status(lyr.default_status), Difficulty(lyr.default_difficulty),
                    lyr.law, lyr.article, Confidence(lyr.confidence), '')

        worst = max(matched, key=lambda r: _DIFF_ORDER.index(r.difficulty))
        return (Status(worst.status), Difficulty(worst.difficulty),
                worst.law or lyr.law, worst.article or lyr.article,
                Confidence(worst.confidence), worst.reason_template)


# ======================================================================
def build_vworld_providers() -> list[VworldLayerProvider]:
    """활성화된 규제 레이어 전체에 대한 어댑터 목록 (지적·이격 기초는 제외)."""
    from ..models import RegulationLayer                        # 지연 import

    qs = (RegulationLayer.objects
          .filter(is_active=True, provider='VWORLD')
          .exclude(role__in=['PARCEL', 'DISTANCE'])
          .order_by('display_order'))
    return [VworldLayerProvider(l) for l in qs]
