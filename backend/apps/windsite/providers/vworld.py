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

    # ------------------------------------------------------------------
    # 구역(폴리곤) 조회
    #
    # 서버 실측으로 확인된 제약이 둘 있다.
    #   1) geomFilter가 polygon/box면 **요청면적 10km² 이내**여야 한다.
    #      (3,000m 정사각형 9.00km² 통과 / 3,162m 10.00km² 거절 — 서버는
    #       4326으로 되돌린 면적을 재므로 실면적보다 1.6% 크게 나온다)
    #   2) POINT+buffer에는 면적 상한이 **없다**. 100km 반경도 받는다.
    #
    # 그래서 레이어를 밀도로 갈라 쓴다. 대부분의 규제 레이어는 피처가 적어
    # 외접원 1회로 끝나고, 연속지적도·건물처럼 조밀한 것만 타일로 나눈다.
    # 어느 쪽인지는 하드코딩하지 않고 건수를 먼저 물어 정한다.
    # ------------------------------------------------------------------

    @classmethod
    def count(cls, layer_id: str, lat: float, lng: float, radius_m: int) -> int | None:
        """
        반경 안의 피처 수. **조회 실패면 None, 피처가 없으면 0.**

        이 둘을 반드시 갈라야 한다. V-World는 결과가 없을 때 status='NOT_FOUND'를
        주는데, 이걸 실패로 묶으면 "규제구역이 없다"가 "조회하지 못했다"로 둔갑한다.
        반대로 실패를 0으로 묶으면 사업에 유리한 쪽으로 잘못 판정하게 된다.

        size=1·geometry=false라 응답이 수백 바이트다. 이 한 번으로 타일 분할
        여부를 정할 수 있으므로, 조밀한 레이어에서 큰 응답을 헛받는 것보다 싸다.
        """
        payload = cls.fetch(layer_id, lat, lng, radius_m, size=1, geometry=False)
        st = cls.status_of(payload)
        if st == 'NOT_FOUND':
            return 0
        if st != 'OK':
            return None
        return cls.total_pages(payload)      # size=1이면 페이지 수 = 피처 수

    @staticmethod
    def fetch_wkt(layer_id: str, wkt: str, size: int = 1000,
                  geometry: bool = True, page: int = 1) -> dict:
        """geomFilter에 도형을 직접 넘겨 조회한다 (버퍼 없음)."""
        params = {
            'service': 'data',
            'request': 'GetFeature',
            'data': layer_id,
            'key': settings.VWORLD_API_KEY,
            'domain': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
            'geomFilter': wkt,
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
    def fetch_all_wkt(cls, layer_id: str, wkt: str, max_pages: int = 10,
                      geometry: bool = True) -> tuple[list[dict], dict]:
        """fetch_all의 도형 버전 — 페이지를 끝까지 넘긴다."""
        collected: list[dict] = []
        first: dict = {}
        total_pages = 1
        page = 1
        while page <= max_pages:
            payload = cls.fetch_wkt(layer_id, wkt, size=cls.MAX_SIZE,
                                    geometry=geometry, page=page)
            if page == 1:
                first = payload
                if cls.status_of(payload) != 'OK':
                    return [], {'payload': payload, 'truncated': False, 'pages': 0,
                                'error': cls.error_text(payload)}
                total_pages = cls.total_pages(payload)
            feats = cls.features(payload)
            collected.extend(feats)
            if len(feats) < cls.MAX_SIZE or page >= total_pages:
                break
            page += 1

        return collected, {'payload': first, 'truncated': total_pages > max_pages,
                           'pages': page, 'total_pages': total_pages, 'error': ''}

    @staticmethod
    def feature_key(f: dict) -> str:
        """중복 제거 키. 타일이 겹치면 같은 피처가 두 번 온다."""
        fid = f.get('id')
        if fid:
            return str(fid)
        props = f.get('properties') or {}
        for k in ('pnu', 'PNU', 'uid', 'id', 'ID'):
            if props.get(k):
                return f'{k}:{props[k]}'
        # 식별자가 없으면 도형으로 가른다. 같은 피처는 같은 좌표열을 갖는다.
        return repr(f.get('geometry'))

    @classmethod
    def fetch_area(cls, layer_id: str, area_geom, geometry: bool = True,
                   max_pages: int = 10) -> tuple[list[dict], dict]:
        """
        사업구역을 덮는 피처를 모두 모은다.

        구역 **밖** 피처도 그대로 돌려준다. 이격거리 판정은 구역 경계 너머의
        주거지·도로까지 봐야 하므로, 자르는 판단은 호출자에게 맡긴다.
        """
        lat, lng, r = geo.circumscribed(area_geom)
        n = cls.count(layer_id, lat, lng, r)
        if n is None:
            payload = cls.fetch(layer_id, lat, lng, r, size=1, geometry=False)
            return [], {'strategy': 'failed', 'truncated': False, 'tiles': 0,
                        'error': cls.error_text(payload) or '조회 실패',
                        'total': None, 'payload': payload}
        if n == 0:
            # 조회는 됐고 해당 없음. 실패와 구분되게 strategy를 남긴다.
            return [], {'strategy': 'empty', 'truncated': False, 'tiles': 1,
                        'total': 0, 'radius_m': r, 'error': ''}

        # 외접원 한 번으로 다 받을 수 있으면 타일을 나눌 이유가 없다
        if n <= cls.MAX_SIZE * max_pages:
            feats, meta = cls.fetch_all(layer_id, lat, lng, r,
                                        max_pages=max_pages, geometry=geometry)
            meta.update(strategy='circle', tiles=1, total=n, radius_m=r,
                        error=meta.get('error', ''))
            return feats, meta

        tiles, tmeta = geo.tiles(area_geom)
        seen: set[str] = set()
        merged: list[dict] = []
        fetched = 0
        truncated = False
        errors: list[str] = []
        for t in tiles:
            feats, meta = cls.fetch_all_wkt(layer_id, geo.wkt_4326(t),
                                            max_pages=max_pages, geometry=geometry)
            if meta.get('error'):
                errors.append(meta['error'])
            truncated = truncated or meta.get('truncated', False)
            fetched += len(feats)
            for f in feats:
                k = cls.feature_key(f)
                if k in seen:
                    continue
                seen.add(k)
                merged.append(f)

        return merged, {
            'strategy': 'tiles',
            'tiles': len(tiles),
            # 인접 타일은 경계를 걸친 피처를 둘 다 돌려준다. fetched와 len(merged)가
            # 같다면 중복 제거가 아무 일도 하지 않은 것이고, 그건 feature_key가
            # 피처를 식별하지 못한다는 뜻이다 — 면적이 부풀어도 티가 안 난다.
            'fetched': fetched,
            'deduped': fetched - len(merged),
            # 타일 상한에 걸리면 구역 일부가 조회되지 않은 것이다. 잘렸다는 사실을
            # 반드시 위로 올려야 한다 — 조용히 넘어가면 '규제 없음'으로 읽힌다.
            'truncated': truncated or tmeta.get('capped', False),
            'tiles_capped': tmeta.get('capped', False),
            'total': n,
            'error': '; '.join(errors[:3]),
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


#: "조회 반경 안에 있기만 하면 저촉"으로 보면 안 되고 **실제로 겹칠 때만**
#: 저촉으로 봐야 하는 레이어.
#:
#: `RegulationLayer.proximity_m`은 모델 주석상 "0이면 교차만 판정"인데,
#: `_judge()`의 `if lyr.proximity_m and measured:`는 0을 거짓으로 평가해
#: 그 필터 자체를 꺼 버린다 — 그 결과 proximity_m=0인 레이어는 설계
#: 의도와 반대로 "조회 반경(터빈 지점이면 500m) 안에 있으면 저촉"으로
#: 동작해 왔다(실측: 삼척 천봉풍력 21·22호기 — 비행금지구역 경계에서
#: 390~451m 떨어져 있는데도 배제로 잡힘. 군사기지법 제10조·시행령
#: 별표5 전문을 확인했으나 이격거리 규정 자체가 없다 — 그 법이 규율하는
#: 것은 "구역 안"에서의 높이·시설 제한이지 "구역 밖 몇 m"가 아니다).
#:
#: 이 버그는 proximity_m=0인 레이어 47개(활성 레이어의 89%) 전부에
#: 해당하지만, 그 필드의 전역 동작을 한 번에 바꾸면 영향 범위가 너무
#: 커서(실측 확인 전인 44개 레이어의 판정이 함께 바뀐다) 여기서는 실측
#: 완료된 공역류 3개만 코드로 짚어 국소적으로 고친다.
EXACT_INTERSECT_ONLY = {'비행금지구역', '비행제한구역', '관제권'}


class VworldLayerProvider(LayerProvider):
    """
    RegulationLayer 한 건을 조회·판정하는 범용 어댑터.

    엔진은 활성 레이어 수만큼 이 어댑터를 생성해 실행한다.
    """

    required_settings = ('VWORLD_API_KEY',)
    data_source = 'V-World 데이터 API'

    def __init__(self, layer, facility_height_m: float | None = None):
        # 발전설비 최고높이. 공역 하한고도와 비교해 저촉 여부를 가른다.
        # 주지 않으면 종전처럼 설정값(풍력 기준)을 쓴다.
        self.facility_height_m = facility_height_m
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
                # 구역 모드에서 radius는 외접원 반지름이라 실제 부지보다
                # 훨씬 넓게 읽힌다. 무엇을 조회했는지 그대로 말한다.
                f'{q.scope_label} {margin}내에서 {self.item_name} 구역이 '
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
            # 거리는 **사업구역 경계 기준**이다. 구역 모드에서 중심점으로
            # 재면 216ha 부지의 중심~경계 1km가 거리에 더해져, 경계 바로
            # 밖 보호구역이 '1.77km 지점'처럼 멀리 읽힌다. 이격 임계
            # (proximity_m) 판정도 같은 이유로 경계에서 재야 한다.
            site = q.geom if q.is_area else geo.point_metric(q.lat, q.lng)
        except geo.GeoUnavailable as e:
            geo_error = str(e)

        hits: list[dict] = []
        for f in feats:
            props = f.get('properties') or {}
            name = (props.get(lyr.name_field) or '').strip() if lyr.name_field else ''
            rec: dict = {'name': name or self.item_name, 'distance_m': None}
            extras = []
            for k in (lyr.extra_fields or [])[:6]:
                if props.get(k):
                    rec[k] = props[k]
                    extras.append(str(props[k]))
            # 서버가 명칭 속성을 주지 않는 레이어(probe_status=NO_NAME)는 항목명만
            # 반복 출력돼 근거가 없다. 부속 속성이라도 붙여야 무엇이 걸렸는지 읽힌다.
            # 예) 산림입지도 → '산림입지도(B₂)' — B₂는 토양형 코드다.
            if not name and extras:
                rec['name'] = f'{self.item_name}({"·".join(extras[:2])})'
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

        # 근접 임계가 설정된 레이어는 임계 밖 피처를 저촉으로 보지 않는다.
        # EXACT_INTERSECT_ONLY는 proximity_m=0이어도(원래는 필터가 꺼지는
        # 값) 강제로 임계 0(=실제 겹침)을 적용한다.
        measured = [h for h in hits if h['distance_m'] is not None]
        exact_only = lyr.code in EXACT_INTERSECT_ONLY
        if (lyr.proximity_m or exact_only) and measured:
            threshold = 0 if exact_only else lyr.proximity_m
            within = [h for h in measured if h['distance_m'] <= threshold]
            if not within:
                nearest = min(measured, key=lambda h: h['distance_m'])
                return self.item(
                    status=Status.POSSIBLE,
                    reason=(
                        f'가장 가까운 {self.item_name}은(는) {nearest["name"]}로 '
                        f'{geo.format_distance(nearest["distance_m"])} 떨어져 있어 '
                        + ('실제로 겹치지 않습니다.' if exact_only else
                           f'근접 판정 기준({threshold:,}m)을 벗어납니다.')
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
        # 참고 레이어(role=CONTEXT)는 규제가 아니다. '중첩됩니다'로 적으면
        # 산림입지도(토양 정보) 같은 항목이 경고처럼 읽힌다.
        if lyr.role == 'CONTEXT':
            head = (f'참고 정보 — 대상 지점의 {self.item_name}: {names}. '
                    '규제 항목이 아니라 현황 참고 자료입니다.'
                    if overlapping else
                    f'참고 정보 — 검토 반경 내 {self.item_name}: {names}.')
        elif overlapping:
            head = f'대상 지점이 {self.item_name} 구역과 중첩됩니다 — {names}.'
        elif nearest:
            basis = '사업구역 경계에서' if q.is_area else '검토 지점에서'
            head = (f'{self.item_name} 경계까지 {basis} '
                    f'{geo.format_distance(nearest["distance_m"])}입니다 — {names}.')
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
        tip_m = float(self.facility_height_m
                      if self.facility_height_m is not None
                      else getattr(settings, 'WINDSITE_TURBINE_TIP_HEIGHT_M',
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
                f'{lowest:,.0f}ft({lowest / _FT_PER_M:,.0f}m)로 발전설비 최고높이 '
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
def build_vworld_providers(facility_height_m: float | None = None
                           ) -> list[VworldLayerProvider]:
    """
    활성화된 규제 레이어 전체에 대한 어댑터 목록 (지적·이격 기초는 제외).

    facility_height_m은 공역 하한고도와의 비교에 쓴다 — 에너지원마다 다르다.
    """
    from ..models import RegulationLayer                        # 지연 import

    qs = (RegulationLayer.objects
          .filter(is_active=True, provider='VWORLD')
          .exclude(role__in=['PARCEL', 'DISTANCE'])
          .order_by('display_order'))
    return [VworldLayerProvider(l, facility_height_m) for l in qs]
