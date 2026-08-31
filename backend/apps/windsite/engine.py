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

from . import energy as energy_mod
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
from .providers.solar_resource import SolarResourceProvider
from .providers.birds import BirdHabitatProvider
from .providers.precedent import PrecedentProvider
from .providers.solar_site import (AcceptanceInfoProvider, CurtailmentInfoProvider,
                                   FarmlandRouteProvider, HeritageSurveyCheckProvider,
                                   SiteObstacleProvider)
from .providers.terrain import SlopeProvider
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

#: 검토 반경 기본값(m). 화면 입력·API 기본값·후보지 비교가 모두 이 값을 쓴다.
#: 반경을 넓히면 조회 필지와 정온시설이 급격히 늘어 판정 범위가 달라지므로
#: 값을 한 곳에서만 정의한다.
DEFAULT_RADIUS_M = 100
#: 입력 허용 범위 — 화면 입력란과 API 검증이 같은 경계를 쓴다.
MIN_RADIUS_M = 50
MAX_RADIUS_M = 20000


def build_providers(sido: str = '', sigungu: str = '', substations=None,
                    energy: str = energy_mod.DEFAULT,
                    usable_m2: float | None = None):
    """
    검토에 사용할 어댑터 목록.

    규제 레이어는 DB(RegulationLayer)에서 읽어 동적으로 구성한다.
    레이어를 추가·수정할 때 코드를 고치지 않기 위함이다.

    에너지원으로 갈리는 것은 둘뿐이다 — **조례 조회 대상**(이격거리가
    에너지원마다 다르다)과 **자원 어댑터**(풍황은 풍력에만 성립한다).
    나머지 규제 레이어는 무엇을 짓든 같은 규정으로 판정되므로 공유한다.
    """
    prof = energy_mod.profile(energy)
    return [
        # 규제 레이어 (DB 정의). 시설 최고높이를 함께 넘겨 공역 저촉을 가른다 —
        # 태양광 모듈(5m)이 접근관제구역에 걸려 조건부로 뜨는 오판을 막는다.
        *build_vworld_providers(prof.facility_height_m),
        CadastralProvider(),                               # 필지·지적 (면적 산출)
        ForestClassificationProvider(),                    # 산지구분 (보전/준보전)
        LandOwnershipProvider(),                           # 소유구분 (국·공유지)
        LandCharacteristicsProvider(),                     # 지형·진입도로
        LandUseZoneProvider(),                             # 필지 지역지구 전체
        EcoNatureMapProvider(),                            # 생태자연도
        # 철새도래지 — 육상태양광 환경성 평가 협의지침의 입지회피지역.
        # 에너지원을 가리지 않는다. 풍력은 조류 충돌이 더 직접적인 쟁점이다.
        BirdHabitatProvider(),
        EcoAreaProvider(),                                 # 생태·경관보전지역
        LandslideProvider(),                               # 산사태위험등급
        HeritageSpatialProvider(),                         # 국가유산 (SHP 적재)
        HeritageSurveyAreaProvider(),                      # 국가유산조사구역 (WMS)
        HeritageDistributionMapProvider(),                 # 문화유적분포지도 (WMS)
        MilitaryZoneProvider(),                            # 군사기지법상 보호구역
        LocalOrdinanceProvider(sido=sido, sigungu=sigungu,
                               energy=prof.code),            # 지자체 조례
        QuietFacilityProvider(sido=sido, sigungu=sigungu,
                              energy=prof.code),             # 정온시설 동심원
        # 지형 경사도 — 조례에 거리가 아닌 조건으로 실리는 항목이다
        SlopeProvider(sido=sido, sigungu=sigungu, energy=prof.code),
        # 계통 연계 — 위치는 OSM, 여유용량은 한전 분산전원 연계정보
        OsmGridProvider(substations=substations),
        # 인허가 사례 대조 — 에너지원을 가리지 않는다. 전기위원회는 3MW 초과
        # 발전사업을 원동력과 무관하게 심의하므로 풍력·태양광 모두 선례가 있다.
        PrecedentProvider(sido=sido, sigungu=sigungu, energy=prof.code),
    ] + (
        # 자원 어댑터는 에너지원마다 하나씩이다. 풍력 검토에 일사량을,
        # 태양광 검토에 풍속을 끼워 넣으면 판정에 쓰이지도 않는 수치가
        # 보고서에 근거처럼 남아 인용된다.
        ([WindResourceProvider()] if prof.has_wind_resource else [])
        + ([SolarResourceProvider()] if prof.has_solar_resource else [])
        # 태양광에만 성립하는 항목. 풍력에 끼워 넣으면 판정에 쓰이지도 않는
        # 수치가 보고서에 근거처럼 남는다.
        + ([FarmlandRouteProvider(),
            SiteObstacleProvider(),
            HeritageSurveyCheckProvider(usable_m2=usable_m2),
            CurtailmentInfoProvider(sido=sido, sigungu=sigungu),
            AcceptanceInfoProvider()] if prof.has_solar_resource else [])
    )


def evaluate(lat: float, lng: float, radius_m: int = DEFAULT_RADIUS_M, address: str = '',
             capacity_mw: float | None = None, sido: str = '', sigungu: str = '',
             substations=None, should_cancel=None,
             energy: str = energy_mod.DEFAULT,
             area_ring: list | None = None,
             area_geom=None,
             usable_m2: float | None = None) -> EvaluationResult:
    # area_ring(또는 area_geom)을 주면 그 도형이 검토 대상이 된다. 주지
    # 않으면 종전처럼 점+반경이다. 경사도처럼 **면에서만 뜻이 서는 항목**은
    # 이 값이 있어야 실제 부지를 재고, 없으면 반경 원을 재게 된다.
    # area_geom은 shapely 도형 그대로다 — 필지 합처럼 멀티폴리곤·홀이 있는
    # 사업구역은 링 하나로 표현되지 않으므로 도형째 넘긴다.
    q = SiteQuery(lat=lat, lng=lng, radius_m=radius_m, address=address,
                  capacity_mw=capacity_mw, area_ring=area_ring,
                  area_geom=area_geom)
    providers = build_providers(sido, sigungu, substations, energy, usable_m2)

    # run()은 예외를 삼키고 항상 AnalysisItem을 반환하므로 병렬 실행이 안전하다.
    #
    # should_cancel을 주면 어댑터를 실행하기 **직전**에 확인한다. 62개를 전부
    # 돌고 나서 확인하면 취소가 1분 넘게 늦어진다 — 남은 조회를 아끼는 것이
    # 취소의 목적이므로 가장 안쪽에서 봐야 한다.
    def _one(p):
        if should_cancel and should_cancel():
            raise _Cancelled()
        return p.run(q)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        items: list[AnalysisItem] = list(pool.map(_one, providers))

    # 저촉 없음(POSSIBLE·LOW)인 참고성 레이어가 화면을 뒤덮지 않도록 정렬만 조정한다.
    # (항목 자체는 근거 보존을 위해 제거하지 않는다)
    items.sort(key=lambda i: (
        ['IMPOSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'POSSIBLE'].index(i.status.value),
        -DIFFICULTY_PENALTY[i.difficulty],
    ))

    overall = summarize(items)
    site_flags = derive_site_flags(items, energy)
    est_mw = _estimated_capacity(usable_m2, energy)

    return EvaluationResult(
        site_info=SiteInfo(
            address=address or f'{lat:.5f}, {lng:.5f}',
            coordinates=Coordinates(lat=lat, lng=lng),
            radius_m=radius_m,
            total_area_m2=q.area_m2,
        ),
        overall_feasibility=overall,
        analysis_items=items,
        # 사업면적을 함께 넘긴다 — 재해영향평가·지표조사·도시계획위원회 심의는
        # 용량이 아니라 **면적**으로 대상이 갈린다.
        permit_roadmap=build_roadmap(
            capacity_mw=capacity_mw if capacity_mw is not None else est_mw,
            capacity_is_estimate=capacity_mw is None and est_mw is not None,
            site_flags=site_flags, energy=energy, area_m2=q.area_m2),
        applicable_laws=collect_laws(energy),
        data_gaps=collect_gaps(items),
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
    )


# ----------------------------------------------------------------------
class _Cancelled(Exception):
    """검토 도중 취소됐다. 호출부가 jobs.Cancelled로 바꿔 올린다."""


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
            radius_m=int(c.get('radius_m', DEFAULT_RADIUS_M)),
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


def _estimated_capacity(usable_m2: float | None, energy: str) -> float | None:
    """
    가용면적에서 **예상 설비용량**을 어림한다. 설계값이 아니다.

    ⚠️ 왜 필요한가 — 이 값이 없으면 환경영향평가 분기가 서지 않는다.

       보고서 PART 1은 「예상 설비용량 약 182MW」를 적으면서, 정작 같은
       문서의 인허가 순서는 「설비용량이 입력되지 않아 판단하지 못했습니다」로
       나갔다. 환산은 보고서 본문에만 있고 로드맵에는 닿지 않았기 때문이다.
       같은 문서가 한 쪽에서는 용량을 알고 다른 쪽에서는 모른다고 말했다.

       면적 환산은 어림이므로 **추정치임을 함께 넘겨**(capacity_is_estimate)
       판정 사유에 「예상 설비용량 기준 — 설계 확정 시 재판정」을 적게 한다.

    면적 환산이 성립하지 않는 발전원(풍력 — 호기 수로 센다)은 None을 낸다.
    """
    per = energy_mod.profile(energy).m2_per_mw
    if not per or not usable_m2 or usable_m2 <= 0:
        return None
    return usable_m2 / per


#: 지목 → 인허가 분기 플래그.
#:
#: 「구거」는 인공 수로다. 농업생산기반시설(용·배수로)인 경우가 많아 목적 외
#: 사용승인 대상이 될 수 있다. 「유지」는 저수지·연못이다. 어느 쪽이든 **관리
#: 주체를 확인해야 한다**는 뜻이지, 절차가 확정된다는 뜻은 아니다 — 그
#: 단서는 절차의 사유 문구가 밝힌다.
JIMOK_FLAGS = {
    '도로': 'ROAD',
    '하천': 'RIVER',
    '구거': 'FARM_INFRA',
    '유지': 'FARM_INFRA',
}


def derive_site_flags(items: list[AnalysisItem],
                      energy: str = energy_mod.DEFAULT) -> set[str]:
    """
    분석 결과에서 인허가 로드맵 분기에 쓸 조건 플래그를 추출한다.
    (예) 농지 포함 → 농지전용허가 단계 활성화
    """
    #: 규제 레이어 code → 로드맵 분기 플래그 (저촉이 확인된 경우에만 부여)
    #
    # ⚠️ '용도지역_농림'을 FOREST로 보지 않는다.
    #
    #    농림지역은 국토계획법 제36조가 정한 **용도지역**으로, 농업진흥지역
    #    **또는** 보전산지를 아우른다. 즉 농림지역이라는 사실만으로는 그 땅이
    #    산지인지 농지인지 가릴 수 없다. 종전에는 이것을 FOREST로 매핑해,
    #    공유수면을 매립해 만든 간척 농지(장흥 216ha·농림지역)에까지
    #    산지전용허가·산지일시사용허가가 인허가 절차로 붙었다.
    #
    #    산지 여부는 아래 세 가지 **실제 산지 증거**로만 판단한다.
    #      ① 지목 임야   ② 산림보호구역   ③ 산지구분 판정에서 산지 확인
    LAYER_FLAGS = {
        '백두대간보호지역': 'BAEKDUDAEGAN',
        '산림보호구역': 'FOREST',
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
            # 국·공유지가 섞여 있으면 사용허가·대부계약 절차가 따로 붙는다.
            if raw.get('public_land') or '국유지' in str(raw.get('ownership', '')):
                flags.add('PUBLIC_LAND')
            # 지목이 곧 증거다 — 부지 안에 하천·구거·도로가 물려 있으면
            # 점용허가·목적 외 사용승인이 따로 필요하다.
            #
            # ⚠️ 종전에는 ROAD·RIVER·FARM_INFRA를 **아무도 켜지 않았다.**
            #    그래서 도로점용허가 같은 절차가 등록돼 있어도 로드맵에서는
            #    영원히 '해당하지 않아 생략 가능'으로만 나왔다.
            for jm, flag in JIMOK_FLAGS.items():
                if jm in jimok:
                    flags.add(flag)
        # 산지구분 판정이 실제로 산지를 확인했을 때만 FOREST. 이 판정은
        # 종전에 아무 데서도 쓰이지 않아, 확인된 보전산지조차 절차에
        # 반영되지 않았다.
        if i.item_name.startswith('산지구분') and raw.get('found'):
            flags.add('FOREST')
        if i.item_name == '토지 소유구분(국·공유지)' and i.status != Status.POSSIBLE:
            flags.add('PUBLIC_LAND')
        if i.item_name == '국가유산·역사문화환경 보존지역' and raw.get('heritages'):
            flags.add('HERITAGE')
        if i.item_name == '군사기지·비행안전구역':
            flags.add('MILITARY')      # 항상 협의 대상으로 취급

        # 도시지역이면 인허가 **경로 자체가 갈린다.**
        #
        # 풍력발전시설은 국토계획법상 기반시설인 「전기공급설비」다
        # (시행령 제2조제1항제3호 유통·공급시설). 법 제43조제1항 본문은
        # 기반시설을 설치하려면 미리 도시·군관리계획으로 결정하라고 하고,
        # 그 예외를 정한 시행령 제35조제1항은 이렇게 갈린다.
        #
        #   제2호 나목  도시지역 및 지구단위계획구역 **밖**에서 「궤도 및
        #               전기공급설비」 → 결정 없이 설치할 수 있다
        #   제1호 가목  도시지역·지구단위계획구역 **안**의 예외 목록에는
        #               전기공급설비가 **없다** → 예외가 아니다
        #
        # 그래서 도시지역에 걸치면 도시·군관리계획으로 도시계획시설을
        # 결정하고 실시계획 인가(법 제88조)를 받아야 한다. 그 경우 법
        # 제56조제1항 단서에 따라 개발행위허가는 받지 않는다 — 두 경로는
        # 함께 밟는 것이 아니라 **갈라지는** 것이다.
        #
        # ⚠️ 저촉 여부는 status가 아니라 overlapping으로 본다. 용도지역은
        #    제약이 아니라 분류라서 겹쳐도 status가 POSSIBLE이다.
        if raw.get('code') == '용도지역_도시' and (raw.get('overlapping') or 0) > 0:
            flags.add('URBAN_AREA')

    # 도시지역이 확인되지 않으면 개발행위허가 경로다. 둘을 각각의 플래그로
    # 두어야 로드맵이 **두 경로를 함께 내지 않는다** — 어느 쪽도 켜지지
    # 않으면 둘 다 '생략 가능'으로 나가 인허가 경로가 통째로 비어 버린다.
    flags.add('URBAN_AREA' if 'URBAN_AREA' in flags else 'NON_URBAN_AREA')
    # 육상풍력은 능선을 타므로 부지 종류를 못 읽었어도 산지로 보는 것이
    # 안전하다(산지전용 단계를 빠뜨리지 않는다). 태양광은 농지·임야·잡종지가
    # 섞여 있어 같은 가정이 성립하지 않으므로 추측하지 않는다.
    if energy_mod.profile(energy).assume_forest_site and not flags & {'FARMLAND', 'FOREST'}:
        flags.add('FOREST')
    return flags
