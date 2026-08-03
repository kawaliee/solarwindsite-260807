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

from datetime import datetime, timezone

from .permits import build_roadmap, collect_laws
from .providers.base import SiteQuery
from .providers.environment import EcoNatureMapProvider, ProtectedAreaProvider
from .providers.others import (
    GridConnectionProvider,
    HeritageProvider,
    LandslideProvider,
    LocalOrdinanceProvider,
    MilitaryAirspaceProvider,
    WindResourceProvider,
)
from .providers.vworld import CadastralProvider, LandUseRegulationProvider
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


def build_providers(sido: str = '', sigungu: str = '', substations=None):
    """검토에 사용할 어댑터 목록 (요청 명세의 10개 레이어)"""
    return [
        LandUseRegulationProvider(),                       # 1
        CadastralProvider(),                               # 2
        EcoNatureMapProvider(),                            # 3
        ProtectedAreaProvider(),                           # 4
        LandslideProvider(),                               # 5
        HeritageProvider(),                                # 6
        MilitaryAirspaceProvider(),                        # 7
        LocalOrdinanceProvider(sido=sido, sigungu=sigungu),  # 8
        WindResourceProvider(),                            # 9
        GridConnectionProvider(substations=substations),   # 10
    ]


def evaluate(lat: float, lng: float, radius_m: int = 500, address: str = '',
             capacity_mw: float | None = None, sido: str = '', sigungu: str = '',
             substations=None) -> EvaluationResult:
    q = SiteQuery(lat=lat, lng=lng, radius_m=radius_m, address=address, capacity_mw=capacity_mw)
    items: list[AnalysisItem] = [p.run(q) for p in build_providers(sido, sigungu, substations)]

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

    penalty = 0
    for i in items:
        penalty += STATUS_PENALTY[i.status]
        if i.status != Status.POSSIBLE:
            penalty += DIFFICULTY_PENALTY[i.difficulty]
    score = max(0, min(100, 100 - penalty))

    if not conditionals and not unknowns:
        grade = Status.POSSIBLE.value
    elif len(unknowns) >= len(items) / 2:
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
    flags: set[str] = set()
    for i in items:
        raw = i.raw or {}
        if i.item_name == '필지·지적 분석':
            jimok = raw.get('jimok', {})
            if any(k in jimok for k in ('전', '답', '과수원')):
                flags.add('FARMLAND')
            if any(k in jimok for k in ('임야',)):
                flags.add('FOREST')
        if i.item_name == 'V-World 토지이용규제':
            zones = ' '.join(raw.get('zones', []))
            if '보전산지' in zones or '산지' in zones:
                flags.add('FOREST')
            if '백두대간' in zones:
                flags.add('BAEKDUDAEGAN')
        if i.item_name == '국가유산·역사문화환경 보존지역' and raw.get('heritages'):
            flags.add('HERITAGE')
        if i.item_name == '군사기지·비행안전구역':
            flags.add('MILITARY')      # 항상 협의 대상으로 취급
    if not flags & {'FARMLAND', 'FOREST'}:
        flags.add('FOREST')            # 육상풍력은 기본적으로 산지 입지로 가정
    return flags
