"""
입지타당성 종합 판정 엔진
---------------------------------------------------------------
각 레이어 어댑터 결과를 모아 종합 점수·등급·요약을 산출하고,
해당 사업에 필요한 인허가 로드맵과 관련 법령 목록을 함께 반환한다.

판정 원칙
  - IMPOSSIBLE이 하나라도 있으면 종합등급도 IMPOSSIBLE (치명 항목 우선)
  - UNKNOWN은 '가능'으로 간주하지 않는다. 미확인 리스크로 감점하고 별도 안내한다.
  - 점수는 상대적 리스크 지표일 뿐, 법적 판단이 아니다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .permits import build_roadmap, collect_laws
from .providers.base import SiteQuery
from .providers.cadastral import CadastralProvider
from .providers.ecoarea import EcoAreaProvider
from .providers.econature import EcoNatureMapProvider
from .providers.heritage_wms import (
    HeritageDistributionMapProvider,
    HeritageSurveyAreaProvider,
)
from .providers.landslide import LandslideProvider
from .providers.local_spatial import HeritageSpatialProvider
from .providers.ned import (
    ForestClassificationProvider,
    LandCharacteristicsProvider,
    LandOwnershipProvider,
    LandUseZoneProvider,
)
from .providers.osm import OsmGridProvider, QuietFacilityProvider
from .providers.wind import WindResourceProvider
from .providers.military import MilitaryZoneProvider
from .providers.others import LocalOrdinanceProvider
from .providers.vworld import build_vworld_providers
from .schemas import (
    DIFFICULTY_PENALTY,
    STATUS_PENALTY,
    AnalysisItem,
    Confidence,
    Coordinates,
    Difficulty,
    EvaluationResult,
    OverallFeasibility,
    SiteInfo,
    Status,
)


logger = logging.getLogger(__name__)

#: 레이어 어댑터 동시 실행 수. V-World 레이어가 수십 개라 순차 실행 시 지연이 크다.
MAX_WORKERS = 6


def build_providers(sido: str = '', sigungu: str = '', substations=None):
    """
    검토에 사용할 어댑터 목록.

    규제 레이어는 DB(RegulationLayer)에서 읽어 동적으로 구성한다.
    레이어를 추가·수정할 때 코드를 고치지 않기 위함이다.
    """
    return [
        *build_vworld_providers(),                         # 규제 레이어 (DB 정의)
        CadastralProvider(),                               # 필지·지적 (면적 산출)
        ForestClassificationProvider(),                    # 산지구분 (보전/준보전)
        LandOwnershipProvider(),                           # 소유구분 (국·공유지)
        LandCharacteristicsProvider(),                     # 지형·진입도로
        LandUseZoneProvider(),                             # 필지 지역지구 전체
        EcoNatureMapProvider(),                            # 생태자연도
        EcoAreaProvider(),                                 # 생태·경관보전지역
        LandslideProvider(),                               # 산사태위험등급
        HeritageSpatialProvider(),                         # 국가유산 (SHP 적재)
        HeritageSurveyAreaProvider(),                      # 국가유산조사구역 (WMS)
        HeritageDistributionMapProvider(),                 # 문화유적분포지도 (WMS)
        MilitaryZoneProvider(),                            # 군사기지법상 보호구역
        LocalOrdinanceProvider(sido=sido, sigungu=sigungu),  # 지자체 조례
        QuietFacilityProvider(sido=sido, sigungu=sigungu),   # 정온시설 동심원
        WindResourceProvider(),                            # 풍황
        # 계통 연계 — 위치는 OSM, 여유용량은 한전 분산전원 연계정보
        OsmGridProvider(substations=substations),
    ]


def evaluate(lat: float, lng: float, radius_m: int = 500, address: str = '',
             capacity_mw: float | None = None, sido: str = '', sigungu: str = '',
             substations=None) -> EvaluationResult:
    q = SiteQuery(lat=lat, lng=lng, radius_m=radius_m, address=address, capacity_mw=capacity_mw)
    providers = build_providers(sido, sigungu, substations)

    # run()은 예외를 삼키고 항상 AnalysisItem을 반환하므로 병렬 실행이 안전하다.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        items: list[AnalysisItem] = list(pool.map(lambda p: p.run(q), providers))

    # 저촉 없음(POSSIBLE·LOW)인 참고성 레이어가 화면을 뒤덮지 않도록 정렬만 조정한다.
    # (항목 자체는 근거 보존을 위해 제거하지 않는다)
    items.sort(key=lambda i: (
        ['IMPOSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'POSSIBLE'].index(i.status.value),
        -DIFFICULTY_PENALTY[i.difficulty],
    ))

    overall = summarize(items)
    site_flags = derive_site_flags(items)

    return EvaluationResult(
        site_info=SiteInfo(
            address=address or f'{lat:.5f}, {lng:.5f}',
            coordinates=Coordinates(lat=lat, lng=lng),
            radius_m=radius_m,
            total_area_m2=q.area_m2,
        ),
        overall_feasibility=overall,
        analysis_items=items,
        permit_roadmap=build_roadmap(capacity_mw=capacity_mw, site_flags=site_flags),
        applicable_laws=collect_laws(),
        data_gaps=collect_gaps(items),
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
    )


# ----------------------------------------------------------------------
def compare(candidates: list[dict]) -> dict:
    """
    복수 후보지를 같은 기준으로 검토해 비교표를 만든다.

    candidates: [{'label','lat','lng','radius_m','address','sido','sigungu','capacity_mw'}]

    각 후보의 검토는 내부적으로 이미 병렬이므로 후보 자체는 순차 실행한다
    (외부 API에 동시요청을 과도하게 보내지 않기 위함).
    """
    rows: list[dict] = []
    results: list[dict] = []

    for idx, c in enumerate(candidates, start=1):
        label = c.get('label') or c.get('address') or f'후보 {idx}'
        res = evaluate(
            lat=float(c['lat']), lng=float(c['lng']),
            radius_m=int(c.get('radius_m', 500)),
            address=c.get('address', ''),
            capacity_mw=c.get('capacity_mw'),
            sido=c.get('sido', ''), sigungu=c.get('sigungu', ''),
            substations=c.get('substations'),
        )
        rows.append({'label': label, **_comparison_row(res)})
        results.append({'label': label, 'result': res.to_dict()})

    # 등급 우선, 그다음 점수 — 점수는 상대 지표이므로 등급을 먼저 본다
    grade_order = ['POSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'IMPOSSIBLE']
    ranked = sorted(rows, key=lambda r: (grade_order.index(r['grade']), -r['score']))
    for rank, r in enumerate(ranked, start=1):
        r['rank'] = rank

    return {
        'comparison': ranked,
        'results': results,
        'note': ('점수·순위는 상대적 리스크 지표이며 법적 판단이 아닙니다. '
                 '순위는 등급을 먼저 보고 점수를 나중에 봅니다. 따라서 '
                 '**미확인(UNKNOWN) 등급 후보는 가용면적·연계거리가 더 좋아도 순위가 밀립니다.** '
                 '데이터 공백을 메우면 순위가 뒤집힐 수 있으므로, 순위만 보지 말고 '
                 'unknown_count와 개별 지표를 함께 확인하십시오.'),
        'compared_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }


def _comparison_row(res: EvaluationResult) -> dict:
    """비교표 1행 — 후보지 간 판단에 실제로 쓰이는 지표만 추린다."""
    items = res.analysis_items
    by_name = {i.item_name: i for i in items}

    parcel = (by_name.get('필지·지적 분석').raw if '필지·지적 분석' in by_name else {}) or {}
    grid = (by_name.get('전력계통 연계(변전소·송전선로)').raw
            if '전력계통 연계(변전소·송전선로)' in by_name else {}) or {}
    quiet = (by_name.get('정온시설 이격거리(동심원 분석)').raw
             if '정온시설 이격거리(동심원 분석)' in by_name else {}) or {}

    subs = grid.get('substations') or []
    facs = quiet.get('facilities') or []
    breached = [r for r in (quiet.get('rings') or []) if r.get('count')]

    blockers = [i.item_name for i in items if i.status == Status.IMPOSSIBLE]
    criticals = [i.item_name for i in items
                 if i.status == Status.CONDITIONAL and i.difficulty == Difficulty.CRITICAL]

    return {
        'score': res.overall_feasibility.score,
        'grade': res.overall_feasibility.grade,
        'coordinates': {'lat': res.site_info.coordinates.lat,
                        'lng': res.site_info.coordinates.lng},
        'radius_m': res.site_info.radius_m,
        'usable_area_m2': parcel.get('usable_area_m2'),
        'total_parcel_area_m2': parcel.get('total_parcel_area_m2'),
        'parcel_count': parcel.get('parcel_count'),
        'conversion_needed': parcel.get('conversion_needed') or {},
        'nearest_substation': (subs[0] if subs else None),
        'nearest_quiet_facility': (facs[0] if facs else None),
        'ordinance_breaches': [f"{r['target']} {r['distance_m']:,}m 내 {r['count']}개소"
                               for r in breached],
        'blockers': blockers,
        'critical_conditions': criticals,
        'unknown_count': sum(1 for i in items if i.status == Status.UNKNOWN),
        'summary': res.overall_feasibility.summary,
    }


# ----------------------------------------------------------------------
def summarize(items: list[AnalysisItem]) -> OverallFeasibility:
    """종합 점수(0~100) 및 등급 산출"""
    if not items:
        return OverallFeasibility(0, Status.UNKNOWN.value, '분석 항목이 없습니다.')

    blockers = [i for i in items if i.status == Status.IMPOSSIBLE]
    conditionals = [i for i in items if i.status == Status.CONDITIONAL]
    unknowns = [i for i in items if i.status == Status.UNKNOWN]

    if blockers:
        names = ', '.join(i.item_name for i in blockers)
        return OverallFeasibility(
            score=0,
            grade=Status.IMPOSSIBLE.value,
            summary=f'{names} 항목이 불가 판정되어 현 부지·배치로는 추진이 어렵습니다. 배치 조정 또는 부지 변경 검토가 필요합니다.',
        )

    # 점수 산식
    #   검토 레이어가 40개를 넘으면서 '항목별 감점 단순합' 방식은 조건부 항목이
    #   몇 건만 나와도 0점으로 포화됐다. 레이어를 늘릴수록 점수가 나빠지는 것은
    #   데이터가 좋아진 것을 나쁜 결과로 표시하는 셈이라 산식을 바꾼다.
    #
    #   worst  : 가장 불리한 항목의 감점 (상태 + 난이도)  — 리스크의 크기
    #   spread : 나머지 조건부 항목 1건당 소액 가산        — 리스크의 개수
    #   gap    : 미확인 항목 1건당 소액 가산               — 정보 부족
    def _weight(i: AnalysisItem) -> int:
        return STATUS_PENALTY[i.status] + DIFFICULTY_PENALTY[i.difficulty]

    worst = max((_weight(i) for i in conditionals + unknowns), default=0)
    spread = 3 * max(0, len(conditionals) - 1)
    gap = 2 * len(unknowns)
    score = max(0, min(100, 100 - (worst + spread + gap)))

    if not conditionals and not unknowns:
        grade = Status.POSSIBLE.value
    elif unknowns and len(unknowns) >= (len(conditionals) + len(unknowns)) / 2:
        # 판정된 것보다 확인 못 한 것이 많으면 '조건부'라고 말할 수 없다
        grade = Status.UNKNOWN.value
    else:
        grade = Status.CONDITIONAL.value

    parts: list[str] = []
    if conditionals:
        crit = [i.item_name for i in conditionals if i.difficulty in (Difficulty.HIGH, Difficulty.CRITICAL)]
        parts.append(
            f'조건부 항목 {len(conditionals)}건' + (f'(중점: {", ".join(crit[:3])})' if crit else '')
        )
    if unknowns:
        parts.append(f'미확인 항목 {len(unknowns)}건 — 데이터 연동 또는 기관 조회 필요')
    ok = [i.item_name for i in items if i.status == Status.POSSIBLE]
    if ok:
        parts.append(f'양호 항목: {", ".join(ok[:3])}')

    return OverallFeasibility(score=score, grade=grade, summary=' · '.join(parts) or '분석 완료')


def collect_gaps(items: list[AnalysisItem]) -> list[str]:
    """미확인 항목을 사용자 안내 문구로 변환"""
    gaps: list[str] = []
    for i in items:
        if i.status == Status.UNKNOWN:
            gaps.append(f'[{i.item_name}] {i.reason} → {i.action_required}')
        elif i.confidence == Confidence.LOW and i.status != Status.POSSIBLE:
            gaps.append(f'[{i.item_name}] 판정 기준이 미검증(LOW) 상태입니다. 법령 원문 대조가 필요합니다.')
    return gaps


def derive_site_flags(items: list[AnalysisItem]) -> set[str]:
    """
    분석 결과에서 인허가 로드맵 분기에 쓸 조건 플래그를 추출한다.
    (예) 농지 포함 → 농지전용허가 단계 활성화
    """
    #: 규제 레이어 code → 로드맵 분기 플래그 (저촉이 확인된 경우에만 부여)
    LAYER_FLAGS = {
        '백두대간보호지역': 'BAEKDUDAEGAN',
        '산림보호구역': 'FOREST',
        '용도지역_농림': 'FOREST',
        '농업진흥지역': 'FARMLAND',
        '국가유산보호구역': 'HERITAGE',
        '개발제한구역': 'GREENBELT',
        '국립자연공원': 'NATIONAL_PARK',
        '도립자연공원': 'NATIONAL_PARK',
        '군립자연공원': 'NATIONAL_PARK',
    }

    flags: set[str] = set()
    for i in items:
        raw = i.raw or {}
        code = raw.get('code', '')

        if code in LAYER_FLAGS and i.status != Status.POSSIBLE:
            flags.add(LAYER_FLAGS[code])

        if i.item_name == '필지·지적 분석':
            jimok = raw.get('by_jimok', {})
            if any(k in jimok for k in ('전', '답', '과수원')):
                flags.add('FARMLAND')
            if '임야' in jimok:
                flags.add('FOREST')
        if i.item_name == '국가유산·역사문화환경 보존지역' and raw.get('heritages'):
            flags.add('HERITAGE')
        if i.item_name == '군사기지·비행안전구역':
            flags.add('MILITARY')      # 항상 협의 대상으로 취급
    if not flags & {'FARMLAND', 'FOREST'}:
        flags.add('FOREST')            # 육상풍력은 기본적으로 산지 입지로 가정
    return flags
