"""
나머지 데이터 레이어 어댑터
---------------------------------------------------------------
 5. 산사태위험등급        (산림청)
 6. 국가유산 (문화재)      (국가유산청)
 7. 군사·비행안전 규제      (국방부 — 공개 API 부재)
 8. 지자체 이격거리 조례    (자체 DB)
 9. 풍황                  (기상청 ASOS + KIER 풍력자원지도)
10. 전력계통 연계          (한전 — 공개 API 부재)
"""
from __future__ import annotations

import math

from django.conf import settings

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery


# ======================================================================
# 5. 산사태위험등급
# ======================================================================
class LandslideProvider(LayerProvider):
    category = '산림'
    item_name = '산사태위험등급'
    data_source = '산림청 산사태위험지도'
    required_settings = ('FOREST_API_KEY', 'FOREST_LANDSLIDE_URL')
    default_law = '산지관리법'
    default_article = '제18조(산지전용허가기준 등) · 시행령 별표4'

    GRADE_RULES = {
        1: (Status.CONDITIONAL, Difficulty.HIGH,
            '산사태위험 1등급지가 포함되어 산지 인허가 심사 시 재해영향 검토가 크게 강화됩니다. '
            '실무상 허가가 어려운 경우가 많아 배치 조정 검토를 권장합니다.'),
        2: (Status.CONDITIONAL, Difficulty.MEDIUM,
            '산사태위험 2등급지가 포함되어 사면 안정성 검토 및 재해저감 대책 수립이 요구됩니다.'),
    }

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        res = self.get(settings.FOREST_LANDSLIDE_URL, {
            'serviceKey': settings.FOREST_API_KEY,
            'lat': q.lat, 'lng': q.lng, 'buffer': q.radius_m, 'type': 'json',
        })
        res.raise_for_status()
        grades = _find_ints(res.json(), keys=('grade', 'gradnm', '등급', 'wrnggrad'))

        if not grades:
            return self.unknown(
                reason='산사태위험등급을 응답에서 판별하지 못했습니다.',
                action_required='산림청 산사태정보시스템에서 대상지 등급을 직접 확인하십시오.',
            )
        worst = min(grades)
        if worst in self.GRADE_RULES:
            st, df, reason = self.GRADE_RULES[worst]
        else:
            st, df, reason = (Status.POSSIBLE, Difficulty.LOW,
                              f'산사태위험 {worst}등급지로 상대적으로 안정적인 구간입니다.')
        return self.item(
            status=st,
            reason=f'검토 반경 내 최고 위험등급 {worst}등급. {reason}',
            difficulty=df,
            confidence=Confidence.LOW,
            source_url='https://sansatai.forest.go.kr',
            action_required='재해영향평가 대상 여부 및 사방시설 계획을 검토하십시오.',
            raw={'grades': sorted(grades)},
        )


# ======================================================================
# 6. 국가유산 (문화재)
# ======================================================================
class HeritageProvider(LayerProvider):
    category = '안전/문화재'
    item_name = '국가유산·역사문화환경 보존지역'
    data_source = '국가유산청'
    required_settings = ('HERITAGE_API_KEY', 'HERITAGE_URL')
    default_law = '문화유산의 보존 및 활용에 관한 법률'
    default_article = '제13조(역사문화환경 보존지역의 보호)'

    #: 국가지정유산 외곽경계 500m 원칙 — 시·도 조례로 축소·확대 가능
    DEFAULT_BUFFER_M = 500

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        res = self.get(settings.HERITAGE_URL, {
            'serviceKey': settings.HERITAGE_API_KEY,
            'lat': q.lat, 'lng': q.lng,
            'radius': q.radius_m + self.DEFAULT_BUFFER_M, 'type': 'json',
        })
        res.raise_for_status()
        items = _find_named_items(res.json())

        if not items:
            return self.item(
                status=Status.POSSIBLE,
                reason=(
                    f'검토 반경 + 역사문화환경 보존지역 기본 {self.DEFAULT_BUFFER_M}m 범위 내에서 '
                    '지정 국가유산이 조회되지 않았습니다.'
                ),
                difficulty=Difficulty.LOW,
                confidence=Confidence.MEDIUM,
                action_required='매장유산 지표조사 대상 여부는 사업 면적 기준으로 별도 확인이 필요합니다.',
                raw={'heritages': []},
            )

        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'인근 국가유산이 확인되었습니다({", ".join(items[:5])}). '
                '역사문화환경 보존지역은 지정유산 외곽경계로부터 원칙적으로 500m 이내이며, '
                '시·도 조례로 범위가 조정될 수 있어 현상변경 허용기준 확인이 필요합니다.'
            ),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.MEDIUM,
            source_url='https://www.khs.go.kr',
            action_required='관할 시·도의 현상변경 허용기준을 확인하고 필요 시 현상변경 허가를 신청하십시오.',
            raw={'heritages': items},
        )


# ======================================================================
# 7. 군사·비행안전 규제 — 공개 API 부재
# ======================================================================
class MilitaryAirspaceProvider(LayerProvider):
    category = '안전/문화재'
    item_name = '군사기지·비행안전구역'
    data_source = '국방부/관할부대 (공개 API 미제공)'
    required_settings = ()
    default_law = '군사기지 및 군사시설 보호법'
    default_article = '제10조(비행안전구역에서의 금지 또는 제한) · 제13조(협의)'

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        return self.item(
            status=Status.UNKNOWN,
            reason=(
                '군사기지 및 군사시설 보호구역·비행안전구역과 항공 레이더 전파영향은 '
                '좌표 기반 공개 API가 제공되지 않아 자동 판정이 불가합니다. '
                '풍력발전기는 높이가 커 표면높이 제한 및 레이더 간섭 검토 대상이 되는 경우가 많습니다.'
            ),
            difficulty=Difficulty.HIGH,
            confidence=Confidence.MEDIUM,
            source_url='https://www.mnd.go.kr',
            action_required=(
                '① 지자체를 경유해 관할부대에 군사시설 보호구역 저촉 여부 질의 '
                '② 표면높이 초과 시 관할부대심의위원회 협의(비행안전영향 검토) '
                '③ 공군 레이더 전파영향 검토 요청'
            ),
        )


# ======================================================================
# 8. 지자체 이격거리 조례 — 자체 DB
# ======================================================================
class LocalOrdinanceProvider(LayerProvider):
    category = '지자체 조례'
    item_name = '지자체 이격거리 조례'
    data_source = '내부 조례 DB'
    required_settings = ()
    default_law = '지자체 도시·군계획 조례'

    def __init__(self, sido: str = '', sigungu: str = ''):
        self.sido = sido
        self.sigungu = sigungu

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from ..models import LocalOrdinance   # 지연 import (앱 로딩 순서)

        if not self.sigungu:
            return self.unknown(
                reason='행정구역(시·군·구)을 특정하지 못해 조례를 조회할 수 없습니다.',
                action_required='주소 또는 행정구역을 입력하면 조례를 조회합니다.',
            )

        qs = LocalOrdinance.objects.filter(sigungu=self.sigungu, energy_type__in=['WIND', 'ALL'])
        if self.sido:
            qs = qs.filter(sido=self.sido)
        rules = list(qs)

        if not rules:
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    f'{self.sido} {self.sigungu}의 풍력 이격거리 조례가 내부 DB에 등록되어 있지 않습니다. '
                    '조례는 지자체별 편차가 크고 개정이 잦아 임의 판단하지 않습니다.'
                ),
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 도시·군계획 조례를 확인해 DB에 등록하십시오.',
            )

        lines = []
        worst_diff = Difficulty.LOW
        for r in rules:
            lines.append(f'{r.target}: {r.distance_m:,}m ({r.ordinance_name} {r.article})')
            if ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(r.difficulty) > \
               ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(worst_diff.value):
                worst_diff = Difficulty(r.difficulty)

        low_conf = [r for r in rules if r.confidence == 'LOW']
        return self.item(
            status=Status.CONDITIONAL,
            reason=(
                f'{self.sido} {self.sigungu} 이격거리 기준 — ' + ' / '.join(lines) +
                '. 실제 저촉 여부는 대상 정온시설·주거지의 실측 거리 확인이 필요합니다.'
            ),
            difficulty=worst_diff,
            law=rules[0].ordinance_name,
            article=rules[0].article,
            confidence=Confidence.LOW if low_conf else Confidence.MEDIUM,
            source_url=rules[0].source_url,
            action_required='현행 조례 원문을 재확인하고 실측 이격거리를 산출하십시오.',
            raw={'rules': lines},
        )


# ======================================================================
# 9. 풍황
# ======================================================================
class WindResourceProvider(LayerProvider):
    category = '사업성'
    item_name = '풍황(연평균 풍속)'
    data_source = '기상청 ASOS / KIER 풍력자원지도'
    required_settings = ('KMA_API_KEY',)
    default_law = '해당 없음 (비규제 · 사업성 판단 영역)'

    #: 업계 통상 참고치 — 법정 기준이 아님
    REFERENCE_MS = 6.0

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        return self.item(
            status=Status.UNKNOWN,
            reason=(
                'KIER 풍력자원지도는 좌표 기반 공개 API가 확인되지 않아 허브고도(100~140m) 풍속을 '
                '자동 추정하지 못했습니다. 기상청 ASOS는 지상 10m 관측값이라 허브고도 환산에 '
                '별도 연직분포(전단지수) 가정이 필요해 단독 판정 근거로 쓰지 않습니다.'
            ),
            difficulty=Difficulty.MEDIUM,
            confidence=Confidence.LOW,
            source_url='https://kier-wind.org',
            action_required=(
                f'① KIER 풍력자원지도에서 대상지 허브고도 풍속 조회 '
                f'② 사업 확정 전 현장 풍황계측(통상 1년 이상) 수행. '
                f'참고: 국내 육상풍력은 통상 연평균 {self.REFERENCE_MS}m/s 이상 지역에 입지하는 것으로 '
                f'보고되나, 이는 법정 기준이 아닌 업계 참고치입니다.'
            ),
        )


# ======================================================================
# 10. 전력계통 연계
# ======================================================================
class GridConnectionProvider(LayerProvider):
    category = '인프라'
    item_name = '전력계통 연계'
    data_source = '한국전력공사 (공개 API 미제공)'
    required_settings = ()
    default_law = '송·배전용 전기설비 이용규정'

    def __init__(self, substations: list[dict] | None = None):
        """substations: [{'name':..,'lat':..,'lng':..,'kv':154}] — 내부 DB/수기 입력"""
        self.substations = substations or []

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not self.substations:
            return self.item(
                status=Status.UNKNOWN,
                reason=(
                    '변전소 위치 및 계통 여유용량은 한전이 공개 API로 제공하지 않아 '
                    '자동 판정이 불가합니다.'
                ),
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.MEDIUM,
                source_url='https://online.kepco.co.kr',
                action_required=(
                    '① 한전ON에서 인근 변전소 접속 가능 용량 조회 '
                    '② 한전에 계통연계 사전검토(기술검토) 신청'
                ),
            )

        nearest = min(self.substations, key=lambda s: _haversine_km(q.lat, q.lng, s['lat'], s['lng']))
        dist = _haversine_km(q.lat, q.lng, nearest['lat'], nearest['lng'])
        kv = nearest.get('kv', 0)

        if dist <= 10 and kv >= 154:
            st, df = Status.POSSIBLE, Difficulty.LOW
            msg = '연계 여건이 양호합니다.'
        elif dist <= 25:
            st, df = Status.CONDITIONAL, Difficulty.MEDIUM
            msg = '송전선로 신설 거리가 길어 공사비·인허가 부담이 있습니다.'
        else:
            st, df = Status.CONDITIONAL, Difficulty.HIGH
            msg = '계통 연계점이 원거리에 있어 사업성 저하 요인입니다.'

        return self.item(
            status=st,
            reason=f'최근접 변전소 {nearest["name"]}({kv}kV)까지 직선거리 약 {dist:.1f}km. {msg}',
            difficulty=df,
            confidence=Confidence.LOW,
            action_required='한전에 계통연계 사전검토를 신청해 실제 접속 가능 용량을 확인하십시오.',
            raw={'nearest': nearest, 'distance_km': round(dist, 2)},
        )


# ----------------------------------------------------------------------
def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _find_ints(data, keys: tuple[str, ...]) -> set[int]:
    out: set[int] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif any(kw in str(k).lower() for kw in keys):
                    try:
                        out.add(int(str(v).strip()[0]))
                    except (ValueError, IndexError):
                        pass
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(data)
    return {g for g in out if 1 <= g <= 5}


def _find_named_items(data) -> list[str]:
    names: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif isinstance(v, str) and ('nm' in str(k).lower() or 'name' in str(k).lower()):
                    if v.strip():
                        names.append(v.strip())
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(data)
    return sorted(set(names))
