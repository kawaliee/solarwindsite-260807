"""
인허가 로드맵 생성
---------------------------------------------------------------
DB(PermitStep)에 등록된 육상풍력 인허가 절차를 사업 조건(용량·부지 특성)에
맞춰 필터링하고, 각 단계의 해당 여부와 사유를 붙여 반환한다.

⚠️ 육상풍력은 「해상풍력 보급 촉진 및 산업 육성에 관한 특별법」(2026-03-26 시행)의
   적용을 받지 않는다. 따라서 아래 로드맵은 개별 인허가 체계를 전제로 한다.
"""
from __future__ import annotations

from . import energy as energy_mod
from .schemas import Confidence, PermitStepResult


#: conditional_on 키 → 사람이 읽는 설명
FLAG_LABEL = {
    'FARMLAND': '부지에 농지(전·답·과수원)가 포함된 경우',
    'FOREST': '부지에 산지가 포함된 경우',
    'GRASSLAND': '부지에 초지가 포함된 경우',
    'HERITAGE': '인근에 지정 국가유산이 있는 경우',
    'MILITARY': '군사시설 보호구역·비행안전구역에 해당하는 경우',
    'BAEKDUDAEGAN': '백두대간 보호지역에 해당하는 경우',
    'ROAD': '도로 점용이 필요한 경우',
    'RIVER': '하천 점용이 필요한 경우',
    'PUBLIC_LAND': '부지에 국·공유지가 포함된 경우',
    'FARM_INFRA': '농업생산기반시설(용·배수로 등)이 포함된 경우',
    'GREENBELT': '개발제한구역에 해당하는 경우',
    'NATIONAL_PARK': '자연공원에 해당하는 경우',
    'EIA': '환경영향평가 대상 규모인 경우',
    'SMALL_EIA': '소규모환경영향평가 대상인 경우',
    'URBAN_AREA': '부지가 도시지역에 걸치는 경우',
    'NON_URBAN_AREA': '부지가 도시지역 밖(관리·농림·자연환경보전지역)인 경우',
}


#: 환경영향평가 계열 — **서로 배타**라 하나만 살아남는다. 선행 조건이
#: 살아남지 못한 쪽을 가리키면 살아남은 쪽으로 갈아 끼운다.
EIA_FAMILY = ('EIA', 'SMALL_EIA')


def build_roadmap(capacity_mw: float | None = None,
                  site_flags: set[str] | None = None,
                  energy: str = energy_mod.DEFAULT,
                  area_m2: float | None = None,
                  capacity_is_estimate: bool = False) -> list[PermitStepResult]:
    from .models import PermitStep

    prof = energy_mod.profile(energy)
    flags = site_flags or set()
    # 에너지원별 절차만 낸다. 등록된 절차가 없으면 빈 목록이 나가고
    # 화면이 '미등록'으로 알린다 — 풍력 절차를 태양광 로드맵으로 보여 주면
    # 발전사업허가 용량 경계(풍력 3MW초과 산업부 / 태양광 3MW 이하 시도)부터
    # 어긋난 문서가 사업 판단에 쓰인다.
    steps = (PermitStep.objects
             .filter(is_active=True, energy_type__in=[prof.code, 'ALL'])
             .order_by('order'))

    out: list[PermitStepResult] = []
    for s in steps:
        applicable = True
        reason = f'모든 {prof.label} 사업 공통 절차입니다.'

        if s.conditional_on:
            key = s.conditional_on
            if key in EIA_FAMILY:
                applicable, reason = _eia_applicability(
                    key, capacity_mw, capacity_is_estimate)
            elif key in AREA_RULES:
                applicable, reason = _area_applicability(key, area_m2)
            else:
                applicable = key in flags
                reason = (
                    f'{FLAG_LABEL.get(key, key)}에 해당하여 필요합니다.'
                    if applicable else
                    f'{FLAG_LABEL.get(key, key)}에 해당하지 않아 생략 가능합니다. '
                    f'(부지 확정 후 재확인 권장)'
                )

        eia_key = s.conditional_on if s.conditional_on in EIA_FAMILY else ''
        out.append(PermitStepResult(
            order=s.order,
            phase=s.get_phase_display(),
            name=s.name,
            authority=s.authority,
            law=s.law,
            article=s.article,
            statutory_days=s.statutory_days,
            statutory_basis=s.statutory_basis,
            conditional_on=s.conditional_on,
            depends_on=list(s.depends_on or []),
            applicable=applicable,
            applicability_reason=reason,
            confidence=Confidence(s.confidence),
            source_url=s.source_url,
            note=s.note,
        ))
        out[-1].eia_key = eia_key
    return _relink_depends(out)


def _relink_depends(steps: list[PermitStepResult]) -> list[PermitStepResult]:
    """
    선행 조건이 **빠진 절차를 가리키지 않게** 고친다.

    ⚠️ 종전 문제 — 개발행위허가의 선행 조건이 「소규모 환경영향평가」로
       못 박혀 있었다. 그런데 이 사업은 182MW라 환경영향평가로 갈려
       소규모평가가 로드맵에서 빠진다. 그러면 순서표는 **없는 절차를
       선행 조건으로 가리키고** 있게 된다.

    환경영향평가 계열은 둘 중 하나만 살아남으므로 살아남은 쪽으로 갈아
    끼운다. 그 밖에 해당하지 않게 된 절차는 선행 조건에서 뺀다 — 밟지
    않을 절차를 기다리라고 적어 둘 수는 없다.
    """
    on = {x.name for x in steps if x.applicable}
    eia_alive = next((x.name for x in steps
                      if x.applicable and getattr(x, 'eia_key', '')), '')
    eia_all = {x.name for x in steps if getattr(x, 'eia_key', '')}

    for st in steps:
        out = []
        for d in st.depends_on:
            if d in on:
                out.append(d)
            elif d in eia_all and eia_alive:
                out.append(eia_alive)      # 소규모 ↔ 본평가 갈아 끼우기
        # 차례는 지키되 중복은 지운다.
        st.depends_on = list(dict.fromkeys(out))
    return steps


# ----------------------------------------------------------------------
# 면적으로 갈리는 절차
# ----------------------------------------------------------------------
#: 용량이 아니라 **사업면적**으로 대상이 갈리는 절차들.
#:
#: `threshold_m2`가 None이면 **대상 규모 기준을 원문에서 확정하지 못했다**는
#: 뜻이다(대개 별표에만 있고 API로 별표 본문을 받지 못한 경우). 그때는 절차를
#: 로드맵에서 지우지 않는다 — 지우면 "해당 없음"으로 읽힌다. 대신 종류로는
#: 해당한다는 사실과 **규모 기준은 확인이 필요하다**는 사실을 함께 밝힌다.
AREA_RULES = {
    'AREA_DISASTER': {
        'kind': '에너지 개발',
        'basis': '자연재해대책법 제5조제1항제3호',
        'scope': '자연재해대책법 시행령 제6조제1항 별표1',
        # 별표1 제목까지는 확인했으나(「재해영향평가등의 협의 대상 행정계획
        # 및 개발사업의 범위 및 협의시기」) 본문 수치는 받지 못했다.
        'threshold_m2': None,
    },
    'AREA_CITY_COMMITTEE': {
        'kind': '개발행위허가 대상 토지의 형질변경',
        'basis': '국토의 계획 및 이용에 관한 법률 제59조제1항',
        'scope': '같은 법 시행령 제57조제1항제1호(제55조제1항 규모 이상)',
        # 시행령 제55조제1항 — **원문 대조 완료**.
        #   관리지역 3만m² · 농림지역 3만m² · 자연환경보전지역 5천m²
        # 용도지역마다 다르므로 값 하나로 가를 수 없다. 3만m² 이상이면 어느
        # 용도지역이든 대상이고, 5천m² 미만이면 어느 쪽이든 아니다. 그
        # 사이는 용도지역을 봐야 정해진다 — 그 사실을 그대로 적는다.
        'threshold_m2': 30_000,
        'threshold_low_m2': 5_000,
        'between': '용도지역에 따라 갈립니다 — 자연환경보전지역은 5,000m², '
                   '관리·농림지역은 30,000m²가 기준입니다',
    },
    'AREA_HERITAGE': {
        # 2024-02-13 개정으로 사업시행자의 지표조사 의무가 매장유산법 구 제6조에서
        # 「국가유산영향진단법」 제9조로 옮겨갔다(원문 대조 완료). 매장유산법을
        # 근거로 적으면 폐지된 조문을 가리키게 된다.
        'kind': '국가유산 영향진단 (매장유산 지표조사 포함)',
        'basis': '국가유산영향진단법 제9조(영향진단의 대상 및 시기)',
        'scope': '같은 법 시행령(대상 사업 규모 기준)',
        'threshold_m2': None,
    },
}


def _area_applicability(key: str, area_m2: float | None) -> tuple[bool, str]:
    """면적 기준 절차의 해당 여부. 기준을 모르면 **모른다고 적는다.**"""
    rule = AREA_RULES[key]
    size = f'사업면적 {area_m2 / 10_000:,.1f}ha' if area_m2 else '사업면적 미상'
    thr = rule['threshold_m2']

    if thr is None:
        return True, (
            f'「{rule["kind"]}」은 {rule["basis"]}이 정한 협의·심의 대상 종류에 '
            f'해당합니다(원문 대조 완료). 다만 **대상 규모 기준은 '
            f'{rule["scope"]}에 있어 원문으로 확인하지 못했습니다** — '
            f'◇ {size} 기준으로 대상인지 인허가청에 확인하십시오.')
    if area_m2 is None:
        return False, (
            f'◇ 확인 필요 — 사업면적이 없어 판단하지 못했습니다. '
            f'{rule["scope"]}은 {thr:,}m² 이상을 대상으로 합니다.')
    if area_m2 >= thr:
        return True, (f'{size}로 {rule["scope"]}의 기준({thr:,}m² 이상)에 '
                      f'해당합니다. (원문 대조 완료)')

    low = rule.get('threshold_low_m2')
    if low and area_m2 >= low:
        return True, (
            f'◇ 확인 필요 — {size}는 {rule["between"]}. 부지의 용도지역으로 '
            f'확정하십시오. ({rule["scope"]} · 원문 대조 완료)')
    return False, (f'{size}로 {rule["scope"]}의 기준({low or thr:,}m² 이상)에 '
                   f'미치지 않아 생략 가능합니다. (부지 확정 후 재확인 권장)')


#: 환경영향평가 대상 용량(MW). **원문 대조 완료** (2026-08).
#:
#: 환경영향평가법 시행령 별표3 —
#:   "시설용량이 1만 킬로와트 이상인 발전소. 다만, … **태양력ㆍ풍력**, 연료전지
#:    발전소 또는 발전사업용 전기저장장치의 경우에는 발전시설용량이
#:    **10만 킬로와트 이상**인 것"
#:
#: 일반 발전소는 10MW인데 태양광·풍력은 100MW로 따로 정해져 있다. 일반
#: 기준을 그대로 적용하면 10MW짜리 태양광이 환경영향평가 대상으로 잡혀
#: 사업 일정이 통째로 어긋난다.
EIA_CAPACITY_MW = 100

#: 위 기준의 근거 조문 표기
EIA_BASIS = '환경영향평가법 시행령 별표3'


#: 경계값 부근으로 보는 폭. 이 안이면 **두 절차를 모두 안내**한다.
#:
#: 면적 환산 어림값은 설계가 확정되면 쉽게 몇 %씩 움직인다. 100MW 경계에서
#: 한쪽만 안내하면, 설계값이 조금 달라지는 것만으로 밟아야 할 절차가 통째로
#: 바뀐다 — 그 사실을 미리 알려야 일정을 잡을 수 있다.
EIA_EDGE_RATIO = 0.10


def _eia_applicability(key: str, capacity_mw: float | None,
                       is_estimate: bool = False) -> tuple[bool, str]:
    """
    환경영향평가 / 소규모환경영향평가 대상 여부. **둘은 동시에 해당하지 않는다.**

    근거 — 환경영향평가법 제43조제1항은 「다음 각 호 **모두**에 해당하는
    개발사업」을 소규모평가 대상으로 하고, 제2호가 「환경영향평가 대상사업의
    종류 및 범위에 해당하지 **아니하는** 개발사업」이다. (원문 대조 완료)

    ⚠️ 다만 경계값 부근(±10%)은 예외다. 어림값 하나로 결론이 뒤집히는
       구간이므로 **두 절차를 모두 안내**하고 그 사실을 밝힌다.

    `is_estimate`는 용량이 사용자가 입력한 설계값이 아니라 가용면적에서
    환산한 어림값이라는 뜻이다. 그때는 판정 문구에 「예상 설비용량 기준 —
    설계 확정 시 재판정」을 반드시 붙인다.
    """
    if capacity_mw is None:
        if key == 'EIA':
            return False, (
                f'◇ 확인 필요 — 설비용량이 없어 판단하지 못했습니다. '
                f'{EIA_BASIS}은 태양력ㆍ풍력의 경우 {EIA_CAPACITY_MW:,}MW(10만kW) '
                f'이상을 대상으로 합니다. **설비용량을 입력하면 자동으로 '
                f'가려집니다.**')
        return False, (
            '◇ 확인 필요 — 설비용량이 없어 환경영향평가 대상인지 먼저 가려야 '
            '합니다. 두 평가는 환경영향평가법 제43조제1항제2호에 따라 **동시에 '
            '해당하지 않습니다.**')

    over = capacity_mw >= EIA_CAPACITY_MW
    near = abs(capacity_mw - EIA_CAPACITY_MW) <= EIA_CAPACITY_MW * EIA_EDGE_RATIO
    src = ('예상 설비용량' if is_estimate else '설비용량')
    tail = ('  ⚠️ **예상 설비용량 기준입니다 — 설계 확정 시 재판정**'
            '(가용면적 환산 어림값이며 설계 결과가 아닙니다).'
            if is_estimate else '')
    edge = (f'  ⚠️ 기준({EIA_CAPACITY_MW:,}MW)에서 ±{EIA_EDGE_RATIO:.0%} 안이라 '
            f'**두 절차를 모두 안내합니다** — 설계 용량이 확정되면 하나로 '
            f'정해집니다.' if near else '')

    # 경계값 부근이면 둘 다 살린다. 그 밖에는 배타 분기다.
    if near:
        if key == 'EIA':
            return True, (
                f'{src} {capacity_mw:,.0f}MW로 {EIA_BASIS}의 태양력ㆍ풍력 기준'
                f'({EIA_CAPACITY_MW:,}MW 이상) 경계에 있습니다.' + edge + tail)
        return True, (
            f'{src} {capacity_mw:,.0f}MW로 환경영향평가 기준 경계에 있어 소규모 '
            f'환경영향평가 가능성도 함께 둡니다. 최종 대상 여부는 **용량이 아니라 '
            f'보전용도지역 해당 여부와 면적**으로 정해집니다'
            f'(제43조제1항제1호 · 시행령 별표4).' + edge + tail)

    if key == 'EIA':
        if over:
            return True, (
                f'{src} {capacity_mw:,.0f}MW로 {EIA_BASIS}의 태양력ㆍ풍력 기준'
                f'({EIA_CAPACITY_MW:,}MW 이상)에 해당합니다. (원문 대조 완료)'
                + tail)
        return False, (
            f'{src} {capacity_mw:,.0f}MW로 {EIA_BASIS}의 태양력ㆍ풍력 기준'
            f'({EIA_CAPACITY_MW:,}MW 이상)에 미치지 않습니다 — '
            f'소규모 환경영향평가로 갈립니다.' + tail)

    # ── 소규모환경영향평가 ────────────────────────────────────────────
    if over:
        return False, (
            '환경영향평가 대상이므로 소규모 환경영향평가 대상에서 제외됩니다 — '
            '환경영향평가법 제43조제1항제2호는 「환경영향평가 대상사업의 종류 및 '
            '범위에 해당하지 아니하는 개발사업」만을 대상으로 합니다. (원문 대조 완료)')
    return True, (
        f'{src} {capacity_mw:,.0f}MW로 환경영향평가 대상이 아니므로 소규모 '
        f'환경영향평가로 갈립니다(같은 법 제43조제1항제2호). 다만 최종 대상 '
        f'여부는 **용량이 아니라 보전용도지역 해당 여부와 면적**으로 정해지므로'
        f'(제43조제1항제1호 · 시행령 별표4), 부지의 용도지역·면적으로 '
        f'확인하십시오.' + tail)


def _eia_applicability_legacy(key: str, capacity_mw: float | None) -> tuple[bool, str]:
    """종전 풍력 문구 — 참고용으로 남겨 둔다."""
    if capacity_mw is None:
        return True, ('설비용량이 입력되지 않아 대상 여부를 판단하지 못했습니다. '
                      '환경영향평가법 시행령 별표3·별표4로 확인이 필요합니다.')

    if key == 'EIA':
        return True, (
            f'설비용량 {capacity_mw:g}MW 기준 환경영향평가 대상 여부는 '
            '환경영향평가법 시행령 별표3(대상사업의 종류·범위)으로 확정해야 합니다. '
            '국내 육상풍력은 상당수가 소규모환경영향평가로 진행된다는 분석이 있으나, '
            '법정 기준선은 원문 확인이 필요합니다.'
        )
    return True, (
        f'설비용량 {capacity_mw:g}MW 기준 소규모환경영향평가 대상 여부는 '
        '환경영향평가법 시행령 별표4로 확정해야 합니다.'
    )


def collect_laws(energy: str = energy_mod.DEFAULT) -> list[dict]:
    from .models import LawReference

    prof = energy_mod.profile(energy)
    return [
        {
            'name': l.name,
            'category': l.category,
            'purpose': l.purpose,
            'key_articles': l.key_articles,
            'confidence': l.confidence,
            'source_url': l.source_url,
            'verified_at': l.verified_at.isoformat() if l.verified_at else None,
            'note': l.note,
        }
        for l in LawReference.objects.filter(energy_type__in=[prof.code, 'ALL'])
    ]
