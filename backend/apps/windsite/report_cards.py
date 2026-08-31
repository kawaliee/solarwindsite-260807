"""
보고서 구성 요소 (docx) — 대시보드 · GIS 카드 · 리스크 매트릭스 · 부록
---------------------------------------------------------------
인허가 실무 검토서는 **지도가 먼저, 표가 나중**이다. 사업 가부를 정하는
사람이 던지는 물음은 세 개뿐이다.

    지금 이대로 되는가 · 안 되면 무엇을 풀어야 하는가 · 어디가 문제인가

앞의 둘은 PART 1 대시보드가, 마지막은 PART 2 지도 카드가 답한다. 70여 개
규제 전수 검토는 그 답을 뒷받침하는 근거일 뿐이므로 부록으로 내린다 —
본문에 두면 정작 결론이 표 여덟 개 뒤에 묻힌다.

■ 발전원 분기

PART 2에 무엇을 싣는가는 발전원마다 다르다(태양광은 농지, 육상풍력은
산지·지형). 그 차례를 `energy.EnergyProfile.gis_cards`가 쥐고, 이 모듈의
`CARD_BUILDERS`가 열쇠로 실제 렌더 함수를 찾는다 — 분기가 코드가 아니라
표에 있어야 발전원을 늘릴 때 이 파일을 뒤지지 않는다.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from . import available, maps
from .report_style import (
    BRAND, DEFAULT_SECTION_COLOR, INK, MUTED, SECTION_COLORS, STATUS_COLORS,
    _callout, _fmt_area, _fmt_ha, _kpi, _kv, _no_borders, _note, _pct, _run,
    _shade, _sig, _styled_table, _sub_band, cell_image_emu, content_width,
    fix_table, image_width_emu,
)

logger = logging.getLogger(__name__)

#: 태양광 설치면적 어림 — 부지 1MW당 m². 설계 결과가 아니라 환산 어림이다.
SOLAR_M2_PER_MW = 10_000

#: 첫 장 핵심쟁점에 실을 최대 개수. 다 늘어놓으면 '핵심'이 아니게 된다.
CRITICAL_MAX = 5

#: 한국어 문장 끝. 판정 사유는 '~습니다.' '~됩니다.' 꼴이라 이것만으로 끊긴다.
_SENTENCE_END = ('. ', '.\n', '다.', '요.', '음.', '됨.', '함.', '?', '!')


def _summarize(text: str, limit: int = 80) -> str:
    """
    판정 사유를 **완결된 한 문장**으로 줄인다.

    ⚠️ 글자 수로 자르면 안 된다. 종전에는 `text[:60] + '…'`로 잘라
    「자료가 미등재된…」 「경사가 완만하다는 뜻이 아니라 표고…」처럼 말이
    중간에 끊긴 채 보고서에 실렸다. 읽는 사람은 뒤에 무슨 말이 오는지
    알 수 없고, 문서로 유통되면 그 상태로 인용된다.

    그래서 **문장 경계에서 끊는다.**
      1) 첫 문장이 limit 안에 들어오면 그 문장을 그대로 쓴다 (대개 여기서 끝)
      2) 첫 문장이 너무 길면 절 구분(— · ,)에서 끊는다
      3) 그것도 없으면 어절 경계에서 끊고 마침표를 붙인다

    어느 경우에도 낱말이 반토막 나거나 '…'로 흐지부지 끝나지 않는다.
    """
    head = (text or '').split('\n')[0].strip()
    if not head:
        return ''

    first = _first_sentence(head)
    if len(first) <= limit:
        return first

    # 첫 문장이 한 줄에 안 들어간다 — 절 단위로 줄인다.
    for sep in ('—', ' · ', ', '):
        cut = first.rfind(sep, 0, limit)
        if cut > limit * 0.4:
            return first[:cut].rstrip(' ,·—') + '.'

    cut = first.rfind(' ', 0, limit)
    return (first[:cut] if cut > limit * 0.4 else first[:limit]).rstrip(' ,·—') + '.'


def _first_sentence(text: str) -> str:
    """가장 먼저 나타나는 문장 종결 지점까지. 없으면 전체."""
    best = len(text)
    for end in _SENTENCE_END:
        i = text.find(end)
        if i != -1:
            # '다.' 같은 두 글자 종결은 마침표까지 포함해 끊는다.
            stop = i + len(end) if end.endswith(('.', '?', '!')) else i + len(end)
            best = min(best, stop)
    return text[:best].strip()


@dataclass
class Ctx:
    """
    카드 하나가 그리는 데 필요한 것 전부.

    인자를 여덟 개씩 들고 다니면 카드를 하나 늘릴 때마다 호출부를 전부
    고쳐야 한다. 한 덩이로 묶어 두면 카드는 필요한 것만 꺼내 쓴다.
    """
    result: dict
    evals: list
    merged: list
    prof: object
    #: 화면에서 캡처한 제약도 PNG. 없으면 서버가 다시 그린다.
    map_image: bytes | None = None
    #: 화면에서 캡처한 항목별 지도 {항목명 또는 'zoning': PNG}
    env_images: dict = field(default_factory=dict)

    @property
    def total(self) -> float:
        return self.result.get('total_area_m2') or 1.0

    def site(self):
        """
        사업구역 도형 — **모든 지도가 이 접근자만 쓴다.**

        지도마다 도형을 따로 만들면 어느 하나가 변형돼도(단순화·첫 링만
        취하기 등) 아무도 모른다. 접근할 때마다 기준 지문(면적·정점 수·
        WKB 해시)과 대조해, 도형이 어디선가 바뀌었으면 그 자리에서 오류
        로그를 남긴다. 첫 호출의 지문이 기준이 된다.
        """
        from .report_style import site_signature
        g = (self.result.get('geoms') or {}).get('area')
        if g is None:
            return None
        sig = site_signature(g)
        base = self.__dict__.setdefault('_site_sig', sig)
        if sig != base:
            logger.error('사업구역 도형 불일치 — 기준 %s vs 현재 %s', base, sig)
        return g

    def outline(self):
        """
        지도에 **경계선으로 그릴** 도형 — site()와 사실상 같다(부동소수점
        잡음만 0.3m 이내로 다듬음, `available.site_outline` 참조).

        한때 좁은 농로 틈을 닫는 근사를 썼다가 실제 필지보다 최대 8.5%
        넓게 부푼 경계가 나와(5차 실측) 폐기했다 — 판정 도형과 다른 도형을
        그리면 이런 어긋남이 구조적으로 재발한다. 모든 지도가 이 하나를
        공유하고, 지문 검증은 site()와 같은 방식이다. 화면(siteRings)도
        같은 값을 받는다.
        """
        from .report_style import site_signature
        g = (self.result.get('geoms') or {}).get('site_outline')
        if g is None:
            return self.site()
        sig = site_signature(g)
        base = self.__dict__.setdefault('_outline_sig', sig)
        if sig != base:
            logger.error('표시용 외곽 도형 불일치 — 기준 %s vs 현재 %s', base, sig)
        return g


# ======================================================================
# PART 1 — 종합 판정 대시보드
# ======================================================================
def summary_dashboard(doc, ctx) -> None:
    """
    첫 장 — 지도를 보고 난 사람이 바로 다음 행동을 정할 수 있게 한다.

    개요(무엇을 검토했나) → 가용 부지·용량(얼마나 되나) → 핵심쟁점(무엇을
    풀어야 하나) 세 덩이. 종전에는 이 셋이 KPI·판정표·해소경로표·결론상자
    네 군데로 흩어져 같은 숫자가 두 번씩 나왔다.
    """
    _overview(doc, ctx)
    _capacity_kpi(doc, ctx)
    _usable_area(doc, ctx)
    _conclusion(doc, ctx)


def _overview(doc, ctx) -> None:
    """사업 기본 개요 — 발전원·위치·규모·조례 기준을 한 상자에."""
    r, prof = ctx.result, ctx.prof
    layout = r.get('layout')
    parcel = r.get('parcel')
    juris = r.get('jurisdictions') or []

    target = ('발전기 %d기 배치선' % len(layout['turbines']) if layout
              else '필지 %d필 (PNU 기준)' % parcel['count'] if parcel
              else '사업구역(폴리곤)')
    rows = [
        ('발전원', ('육상풍력' if prof.has_wind_resource else prof.label)),
        ('검토 대상', target),
        ('검토 면적', _fmt_area(r['total_area_m2'])),
    ]
    # 사업구역이 그린 폴리곤에서 정제됐으면 그 사실을 밝힌다. 216.9ha를
    # 알고 있는 사람이 190.5ha를 보면 어느 쪽이 틀렸는지 알 수 없다 —
    # 두 값의 관계가 문서 안에 있어야 한다.
    ref = r.get('site_refine') or {}
    if ref.get('na_m2'):
        rows.append(('구역 정제',
                     '그린 구역 %s에서 도로·구거 등 사업 대상 아님 필지 '
                     '%s(%d필지)를 뺀 값입니다.'
                     % (_fmt_ha(ref.get('drawn_m2') or 0),
                        _fmt_ha(ref['na_m2']), ref.get('na_count') or 0)))
    elif ref.get('fallback'):
        rows.append(('구역 정제',
                     '◇ 확인 필요 — 필지 정제에 실패해 그린 폴리곤을 그대로 '
                     '썼습니다 (%s). 구역 안 도로·구거가 판정에 포함되어 '
                     '있을 수 있습니다.' % ref['fallback']))
    cap = _capacity_note(r, prof, len(ctx.evals))
    if cap:
        rows.append(('예상 설비용량', cap))
    if juris:
        rows.append(('관할 지자체',
                     ' · '.join(f'{j["sigungu"]} {j["ratio"] * 100:.1f}%' for j in juris)
                     + '  (검토 면적 대비)'))
    cut = _ordinance_cutoff(r)
    if cut:
        rows.append(('조례 기준일', cut))
    _kv(doc, rows)


def _ordinance_cutoff(result: dict) -> str:
    """경과규정 기준일 — 조례가 언제 것인지 밝혀야 결과를 읽을 수 있다."""
    ords = (result.get('grandfathering') or {}).get('ordinances') or []
    return ' · '.join(f'{o["sigungu"]} {o["cutoff_date"]}' for o in ords[:2])


def _capacity_note(area: dict, prof, n_points: int) -> str:
    """
    예상 설비용량 — 태양광은 협의 포함 가용면적을, 풍력은 호기 수 × 대당
    용량을 쓴다. 사업 단위가 서로 달라 같은 환산을 쓸 수 없다.
    """
    if prof is not None and getattr(prof, 'has_screening', False):
        loose = area.get('available_with_consultation_m2') or 0
        return f'약 {loose / SOLAR_M2_PER_MW:,.0f} MW ({SOLAR_M2_PER_MW:,}m²/MW 가정)'
    per = area.get('capacity_mw_per_turbine')
    if per and n_points:
        return f'약 {per * n_points:,.1f} MW ({n_points}기 × {per:g}MW)'
    return ''


def _capacity_kpi(doc, ctx) -> None:
    """사업구역 4분류 카드 + (풍력) 호기별 판정 한 줄."""
    r = ctx.result
    total = r['total_area_m2'] or 1.0
    _kpi(doc, [
        ('배제', _fmt_ha(r['blocked_m2']), _pct(r['blocked_m2'], total),
         STATUS_COLORS['IMPOSSIBLE']),
        ('조건부', _fmt_ha(r['conditional_m2']), _pct(r['conditional_m2'], total),
         STATUS_COLORS['CONDITIONAL']),
        ('제약 없음', _fmt_ha(r['free_m2']), _pct(r['free_m2'], total),
         STATUS_COLORS['POSSIBLE']),
        ('판정 보류', _fmt_ha(r['pending_m2']), _pct(r['pending_m2'], total),
         STATUS_COLORS['UNKNOWN']),
    ])
    _note(doc,
          '※ 「조건부」는 협의·저감으로 풀 수 있다고 본 값입니다. 실제 설치면적은 '
          '차폐수·울타리 이격, 전기실 부지, 원형보전지 계획으로 더 줄어듭니다.')

    if r.get('layout'):
        _turbine_kpi(doc, ctx)


def _turbine_kpi(doc, ctx) -> None:
    """
    호기별 판정 — 배치를 고칠 단서다.

    풍력은 면적이 아니라 **어느 호기가 걸렸는가**가 다음 행동을 정한다.
    한 기라도 불가면 그 호기를 옮겨야 하고, 옮기면 해소되는지 아닌지가
    사업 성립을 가른다.
    """
    verdicts = _turbine_verdicts(ctx.merged, len(ctx.evals))
    if not verdicts:
        return
    cnt = {k: sum(1 for v in verdicts.values() if v == k)
           for k in ('POSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'IMPOSSIBLE')}
    _kpi(doc, [
        ('총 계획', f'{len(verdicts)}기', '배치선 기준', (BRAND, 'EAF1F9')),
        ('제약 없음', f'{cnt["POSSIBLE"]}기', '그대로 진행', STATUS_COLORS['POSSIBLE']),
        ('조건부', f'{cnt["CONDITIONAL"] + cnt["UNKNOWN"]}기', '협의·확인 필요',
         STATUS_COLORS['CONDITIONAL']),
        ('배제', f'{cnt["IMPOSSIBLE"]}기', '위치 이동 필요',
         STATUS_COLORS['IMPOSSIBLE']),
    ])
    bad = sorted(no for no, st in verdicts.items() if st == 'IMPOSSIBLE')
    if bad:
        _note(doc, '※ 위치 이동이 필요한 호기 — ' + available.nos_label(bad),
              color=STATUS_COLORS['IMPOSSIBLE'][0])


def _turbine_verdicts(merged: list, n_points: int) -> dict:
    """호기 번호 → 그 호기의 **가장 나쁜** 판정. 한 항목이라도 걸리면 그 값이다."""
    if not n_points:
        return {}
    sev = available._SEVERITY
    out = {i: 'POSSIBLE' for i in range(1, n_points + 1)}
    for m in merged:
        for no, st in (m.get('per_point') or {}).items():
            if no in out and sev.get(st, 0) > sev.get(out[no], 0):
                out[no] = st
    return out


def _usable_area(doc, ctx) -> None:
    """
    가용면적 두 가지 — **예상 설비용량의 근거**다.

    종전에는 부록 「면적 분포와 가용면적」에만 있었다. 그런데 PART 1이 적는
    「예상 설비용량 약 182MW」는 바로 이 협의 포함 가용면적을 나눈 값이라,
    부록을 지우면 용량의 근거가 문서에서 통째로 사라진다. 결론 옆에 둔다.
    """
    r = ctx.result
    total = r.get('total_area_m2') or 1.0
    strict = r.get('available_strict_m2')
    loose = r.get('available_with_consultation_m2')
    if strict is None and loose is None:
        return
    _styled_table(
        doc, ['가용면적 구분', '면적', '비율'],
        [['엄격 가용 (제약 없음만)', _fmt_area(strict or 0), _pct(strict or 0, total)],
         ['협의 포함 가용 (+조건부)', _fmt_area(loose or 0), _pct(loose or 0, total)]],
        accent='18907E', widths=[4.4, 7.0, 3.0])
    _note(doc,
          '※ 가용면적은 하나로 말할 수 없어 둘로 병기합니다. 이 검토는 생태자연도 '
          '1등급도 백두대간 핵심구역도 「불가」가 아니라 「조건부」로 봅니다 — '
          '법률상 예외 행위가 있기 때문입니다. **위 예상 설비용량은 「협의 포함 '
          '가용」을 기준으로 환산한 값**이며, 어느 값을 쓸지는 사업 판단입니다.')
    _outside_warning(doc, ctx)


#: 검토 면적 중 시군구 경계 밖 비율이 이 값을 넘으면 경고를 낸다.
OUTSIDE_WARN = 0.05


def _outside_warning(doc, ctx) -> None:
    """
    시군구 경계 밖이 많으면 가용면적을 그대로 읽으면 안 된다.

    경계 밖(해상 등)에는 어떤 조례도 적용되지 않아 「제약 없음」으로 집계된다.
    도서 지역에서는 그 몫이 절반을 넘기도 한다.
    """
    meta = ctx.result.get('jurisdiction_meta') or {}
    ratio = float(meta.get('uncovered_ratio') or 0)
    if ratio < OUTSIDE_WARN:
        return
    _callout(doc, '검토 면적의 %.1f%%가 시군구 경계 밖입니다' % (ratio * 100),
             '해상이거나 행정경계에 포함되지 않은 범위입니다. 이 부분에는 어떤 '
             '지자체 조례도 적용되지 않아 「제약 없음」으로 집계되므로, 위 '
             '가용면적과 예상 설비용량을 그대로 읽지 마십시오. 육상 면적만 '
             '필요하면 사업구역을 육지 안으로 좁혀 다시 검토하십시오.',
             tone='UNKNOWN')


def _conclusion(doc, ctx) -> None:
    """
    PART 1의 끝 — 결론 상자 하나.

    종전에는 이 뒤에 핵심 쟁점 카드 다섯 장이 이어졌다. 그런데 그 다섯은
    전부 PART 2가 항목별로 다시 다루는 것이라(농업진흥지역도·토지 소유구분·
    국가유산 3건), 같은 지도와 같은 판정이 한 문서에 두 번 실렸다. 카드를
    걷어내고 **PART 2를 가리키는 한 줄**로 잇는다.
    """
    issues = _rank_issues(ctx)
    if not issues:
        _callout(doc, '결론 — 사업 추진이 가능합니다',
                 '검토된 항목에 걸림이 없어 현재 배치·부지 기준으로 사업 추진이 '
                 '가능합니다. 항목별 상세는 PART 2에서 다룹니다.', tone='POSSIBLE')
        return

    n = len([m for m in ctx.merged if m['status'] in ('CONDITIONAL', 'UNKNOWN')])
    if any(i['status'] == 'IMPOSSIBLE' for i in issues):
        _callout(doc, '결론 — 현 배치·부지로는 사업이 어렵습니다',
                 '●불가 항목이 해소되지 않으면 현재 배치·부지 그대로는 추진이 '
                 '어렵습니다. 배치 조정 또는 부지 변경을 검토하십시오. '
                 f'조건부·확인 필요 {n}개 항목을 포함한 항목별 상세는 '
                 'PART 2에서 다룹니다.', tone='IMPOSSIBLE')
        return
    _callout(doc, '결론 — 인허가 절차를 해결하면 사업이 가능합니다',
             f'조건부·확인 필요 {n}개 항목의 허가·협의 절차를 밟으면 사업 추진이 '
             '가능합니다. **항목별 상세는 PART 2에서 다루고**, 밟아야 할 절차와 '
             '차례는 PART 3에 있습니다.', tone='CONDITIONAL')


def _rank_issues(ctx) -> list:
    """
    핵심쟁점 선별. 차례가 곧 우선순위다.

      1) 경과규정 — 결론을 통째로 뒤집으므로 **항상 맨 위**
      2) 불가 항목 전부 — 사업을 막는다
      3) 조건부·확인 필요 — (난이도, 걸린 면적) 내림차순

    면적은 `by_reason`에서 이름 앞머리로 잇는다. 판정 항목명과 채색 사유
    이름이 늘 같지는 않아(조례 이격은 주거/도로로 갈린다) 못 찾으면 0으로
    두고 난이도만으로 세운다.
    """
    from .schemas import DIFFICULTY_PENALTY

    out = []
    grand = ctx.result.get('grandfathering') or {}
    if grand.get('review_required'):
        out.append({
            'name': '조례 경과규정 (부칙)',
            'status': 'CONDITIONAL',
            'fact': '발전사업허가일이 조례 시행일보다 앞섬 — ' + (_ordinance_cutoff(ctx.result) or '기준일 확인 필요'),
            'basis': _ordinance_basis(ctx.result),
            'area_m2': 0.0,
            'route': '부칙 경과조치 적용 여부를 관할 지자체와 협의. **면제가 확정된 것은 '
                     '아니며**, 적용되면 조례 이격 제약이 해소됩니다.',
        })

    area_of = _area_by_name(ctx.result)
    hits = _parcel_hits(ctx)

    def rank(m):
        return (-DIFFICULTY_PENALTY.get(m.get('difficulty'), 0),
                -area_of(m['item_name']))

    blockers = [m for m in ctx.merged if m['status'] == 'IMPOSSIBLE']
    others = sorted((m for m in ctx.merged if m['status'] in ('CONDITIONAL', 'UNKNOWN')),
                    key=rank)
    for m in blockers + others:
        if len(out) >= CRITICAL_MAX:
            break
        out.append({
            'name': m['item_name'],
            'status': m['status'],
            'fact': _fact_line(m, area_of, hits=hits),
            'basis': _basis_line(m),
            'area_m2': area_of(m['item_name']),
            'route': _route_note(m['item_name'], m['status'],
                                 m.get('action_required') or ''),
        })
    return out


def _ordinance_basis(result: dict) -> str:
    """
    경과규정의 근거 — 조례 이름과 조문. 판정에 쓴 조례 원문에서 그대로 온다.

    못 찾으면 지어내지 않고 확인 필요로 남긴다.
    """
    ords = (result.get('grandfathering') or {}).get('ordinances') or []
    bits = []
    for o in ords[:2]:
        name = (o.get('ordinance') or '').strip()
        if not name:
            continue
        art = (o.get('article') or '').strip()
        bits.append(f'{name} {art} 부칙'.replace('  ', ' '))
    return ' · '.join(bits) or '◇ 확인 필요 — 조례 부칙 원문 미확인'


def _basis_line(m: dict) -> str:
    """
    근거 칸 — 법령명 + **조문 번호**.

    조문이 없으면 있는 척하지 않는다. 「농지법」이라고만 적힌 근거는 협의
    자리에서 되짚을 수 없어 근거 구실을 못 한다. 그래서 어디까지 확인됐는지를
    그대로 밝힌다.
    """
    law = (m.get('law') or '').strip()
    art = (m.get('article') or '').strip()
    # 근거 칸에 법령이 아닌 안내가 들어오는 항목이 있다 — 「해당 없음(참고
    # 정보)」. 거기에 '조문 확인 필요'를 붙이면 있지도 않은 조문을 찾으라는
    # 말이 된다.
    if any(k in law for k in ('해당 없음', '참고 정보', '없음')):
        return law
    if law and art:
        # 조문 칸이 법령명을 이미 달고 오는 항목이 있다 — 「국유재산법
        # 제30조(사용허가) · 공유재산법 제20조」. 그대로 이어붙이면
        # 「국유재산법 · 공유재산법 국유재산법 제30조…」가 되어 읽히지 않는다.
        if any(part.strip() and part.strip() in art for part in law.split('·')):
            return art
        return f'{law} {art}'
    if law:
        return f'{law} — ◇ 조문 번호 확인 필요'
    return '◇ 확인 필요 — 근거 법령·조문이 확인되지 않았습니다.'


def _area_by_name(result: dict):
    """항목명 → 그 항목이 차지한 면적(m²). 못 찾으면 0."""
    rows = result.get('by_reason') or []

    def find(name: str) -> float:
        for r in rows:
            if r['layer'].startswith(name) or name in r['layer']:
                return r['area_m2']
        return 0.0
    return find


def _fact_line(m: dict, area_of, limit: int = 60, hits: dict | None = None) -> str:
    """현황 한 줄 — 걸린 면적·단위가 있으면 그것부터, 없으면 사유 첫 문장."""
    bits = []
    a = area_of(m['item_name'])
    if a:
        bits.append(_fmt_ha(a))
    unit = _hit_unit(m, hits)
    if unit:
        bits.append(unit)
    head = _summarize(m.get('reason') or '', limit)
    if head:
        bits.append(head)
    return ' · '.join(bits) or '-'


#: 항목명 → 필지 채색의 사유 문구. 채색은 레이어 제목을 사유로 적으므로
#: 대개 항목명과 같지만, 조례처럼 표기가 갈리는 것은 여기서 이어 준다.
_HIT_ALIAS = {
    # 조례 이격은 채색에서 주거(배제)와 축사·정온(조건부)으로 갈린다.
    # 판정 항목은 하나이므로 **둘 다** 세야 필지 수가 맞는다.
    '지자체 이격거리 조례': ('지자체 조례 주거 이격거리',
                             '지자체 조례 축사·정온시설 이격'),
}


def _parcel_hits(ctx) -> dict | None:
    """
    항목별로 '걸린 필지 수'를 센다. 태양광 구역 검토에서만 값이 있다.

    '대상 아님'은 세지 않는다 — 애초에 후보가 아닌 땅이라 넣으면 비율이
    실제와 달라진다.
    """
    scr = ctx.result.get('screening') or {}
    if not scr or scr.get('too_wide'):
        return None
    rows = [p for p in (scr.get('parcels') or [])
            if p.get('grade') != 'NOT_APPLICABLE']
    if not rows:
        return None
    # 채색 사유에는 꼬리말이 붙는다 — '농업진흥지역도 (염도 5.5dS/m …)'.
    # 항목명과 **앞머리로** 맞춰야 필지가 세어진다. 정확히 같은 문자열로
    # 찾으면 하나도 못 찾아 전부 '구역 전체'로 나온다.
    names = {m['item_name'] for m in ctx.merged}
    hits: dict[str, int] = {}
    for nm in names:
        n = sum(1 for p in rows
                if any((r or '').startswith(nm)
                       for r in (p.get('reasons') or [])))
        if n:
            hits[nm] = n
    # 채색 사유는 '— 사업 불가'처럼 꼬리가 붙으므로 앞머리로 맞춘다.
    # 한 항목이 여러 사유로 갈리면 필지를 **겹치지 않게** 센다.
    for item, alias in _HIT_ALIAS.items():
        heads = (alias,) if isinstance(alias, str) else tuple(alias)
        n = sum(1 for p in rows
                if any(r.startswith(heads) for r in (p.get('reasons') or [])))
        if n:
            hits[item] = n
    return hits


def _hit_unit(m: dict, hits: dict | None) -> str:
    """
    걸린 대상의 단위. **발전원마다 다르다.**

    풍력은 배치선의 호기가 단위라 '1·3~5호기'가 곧 배치를 고칠 단서다.
    태양광은 호기가 없다 — 구역 검토는 대표점 한 곳만 돌리므로 '1호기'가
    찍히는데, 그건 아무 뜻도 없는 말이다. 사업 단위인 **필지 수**를 적는다.
    """
    if hits is not None:                       # 태양광 — 필지 채색에서 센 값
        n = hits.get(m['item_name'])
        # 필지로 나뉘지 않는 항목(계통·자원 등)은 아무 말도 하지 않는다.
        # '구역 전체'라고 적으면 칸만 차지하고 뜻은 더해 주지 않는다.
        return f'{n:,}필지' if n else ''
    label = m.get('hit_label')
    return label if label and label != '-' else ''


#: 제약 사유 이름 조각 → 해소 경로. 이름만으로는 "그래서 어떻게 해야
#: 하는가"가 서지 않는 항목이 대부분이라, 항목마다 다음 행동을 붙인다.
#: **차례가 우선순위다** — 앞에서 걸린 것을 쓴다.
_ROUTE_NOTES = (
    ('경과규정 검토 대상',
     '발전사업허가일이 조례 시행일보다 앞서 경과규정(부칙) 적용 여부를 관할 '
     '지자체와 협의 — 면제가 확정된 것은 아님'),
    ('농업진흥', '염도 {saline}dS/m 이상이면 농지 타용도 일시사용허가로 '
                 '태양광 사업 가능 — 토양 염도평가 선행'),
    ('농림지역', '염도 {saline}dS/m 이상이면 농지 타용도 일시사용허가로 '
                 '태양광 사업 가능 — 토양 염도평가 선행'),
    ('주거 이격',
     '이격거리 밖으로 배치 재조정, 또는 인근 주민 대상 주민참여사업으로 수용성 확보'),
    ('도로 이격 (국도',
     '조문이 직접 금지하는 범위 — 이격거리 밖으로 배치 재조정 필요'),
    ('도로 이격 (시·군도',
     '도로 등급(군도 · 농어촌도로) 확인 후 조건부로 해소 가능'),
    ('축사·정온시설', '완충저감시설 설치, 소유자·관계기관 협의로 조건부 해소'),
    ('정온시설', '이격거리 확보 또는 소음 저감대책 협의 — 호기 이설로 해소 가능'),
    ('철새도래지', '환경영향평가 협의, 조류 이동시기 회피 등 저감대책 협의'),
    ('생태자연도', '1등급은 원칙적 회피 — 저감대책 관계기관(환경청) 협의'),
    ('백두대간', '백두대간 핵심구역은 원칙적 회피 — 완충구역은 산림청 협의'),
    ('산사태', '산사태위험 1등급 제척 후 배치. 산지전용 시 재해영향평가 협의'),
    ('산지', '산지전용 · 일시사용 허가 관계기관 협의'),
    ('경사도', '조례 기준 초과 구간 제척 — 실측 측량으로 확정 필요'),
    ('전력계통', '한전 계통 연계 사전검토 신청 — 여유용량은 선점으로 변동'),
    ('군사', '관할 부대 작전성 검토 협의 — 비행안전구역은 고도 제한 확인'),
    ('국가유산', '문화재청·지자체 사전 협의, 필요 시 지표조사'),
)


def _route_note(layer: str, status: str, action: str = '') -> str:
    """해소 경로. 표에 없는 항목은 판정이 이미 만들어 둔 조치 문구를 쓴다."""
    from .providers.solar_site import SALINE_DSM
    for key, note in _ROUTE_NOTES:
        if key in layer:
            return note.format(saline=SALINE_DSM)
    if action:
        return action
    return '배치 재검토 필요' if status == 'IMPOSSIBLE' else '관계기관 협의 필요'


# ── 지도 카드 ─────────────────────────────────────────────────────────
#: 카드의 좌(지도)/우(설명) 폭 비율.
#:
#: 본문 폭 10,200twips 기준으로 지도 칸은 4,896twips = 3.4in, 셀 여백을 뺀
#: 그림은 3.25in다. 종전 3열 격자의 2.05in짜리 지도는 규제 경계가 뭉개져
#: 협의 자료로 쓸 수 없었다 — 3.2in이 그 하한이다.
CARD_RATIO = [0.48, 0.52]

#: 도형이 없을 때 넣는 문구. **빈칸으로 두지 않는다.**
#:
#: 빈칸은 '제약 없음'으로 읽힌다. 지점(반경) 기준으로 판정한 항목은 구역
#: 전체를 덮는 공간자료가 없을 뿐이지, 저촉이 없다는 뜻이 아니다. 그 둘을
#: 가르지 않으면 보고서를 받은 사람이 검토가 끝난 줄로 읽는다.
NO_GEOM = ('구역 도형 없음 — 지점(반경) 기준 판정\n'
           '제약이 없다는 뜻이 아니라, 구역 전체를 덮는 공간자료가 없어 '
           '대표 지점으로 조회했다는 뜻입니다.')


def _log_map(name: str, boundary, png: bytes | None, source: str) -> None:
    """
    §계측 — 지도 하나가 그린 **경계 도형의 지문**과 **산출 PNG의 md5**를 남긴다.

    지도별로 (면적·정점 수·WKB md5)가 전부 같아야 정상이다. 하나라도
    다르면 그 지도의 렌더 경로가 다른 도형을 받고 있다는 뜻이고, 이 로그가
    그 지점을 특정한다. source는 캡처/서버렌더 구분이다.
    """
    import hashlib
    from .report_style import site_signature
    if boundary is not None:
        a, v, _h = site_signature(boundary)
        wkb = hashlib.md5(boundary.wkb).hexdigest()[:12]
    else:
        a = v = 0
        wkb = '-'
    logger.info('지도지문 | %-22s | %-6s | area=%.1f | v=%d | wkb=%s | png=%s',
                name, source, a, v, wkb,
                hashlib.md5(png).hexdigest()[:12] if png else '-')


def _map_card(doc, ctx, e: dict, accent: str, *, show_title: bool = True) -> None:
    """
    지도 카드 한 장 — 왼쪽에 지도, 오른쪽에 판정·현황·근거·해소 경로.

    종전에는 이것이 네 칸짜리 표 한 줄이었다. 「농업진흥지역도 · ▲ 조건부 ·
    656,123평 · 염도평가 선행」이라고 적혀 있어도 **부지의 어디가 걸렸는지**를
    알 수 없어, 읽는 사람이 협의 자리에 들고 갈 수 없었다. 항목마다 지도를
    붙이면 그 한 장이 곧 협의 자료가 된다.
    """
    t = doc.add_table(rows=1, cols=2)
    _no_borders(t)
    widths = fix_table(t, doc, ratios=list(CARD_RATIO))
    left, right = t.rows[0].cells
    _card_map_cell(left, ctx, e, cell_image_emu(widths[0]))
    _card_info_cell(right, e, accent, show_title=show_title)
    doc.add_paragraph()


def _card_map_cell(cell, ctx, e: dict, width_emu: int) -> None:
    """지도 칸 — 지도를 못 그리면 왜 못 그렸는지를 적는다."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Emu

    cell.paragraphs[0].text = ''
    img = _issue_map(ctx, e)
    if img:
        try:
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(io.BytesIO(img), width=Emu(width_emu))
            _run(cell.add_paragraph(), _map_caption(e), size=7.5, color=MUTED)
            return
        except Exception as ex:                                 # noqa: BLE001
            logger.warning('카드 지도 삽입 실패(%s): %s', e['name'], ex)
    _shade(cell, 'F1F3F6')
    for line in NO_GEOM.split('\n'):
        _run(cell.add_paragraph(), line, size=8, color=MUTED)


#: 경계선 캡션 공통 문구. 경계선은 편입 필지 그 자체(판정·면적과 같은
#: 도형)이므로 필지 사이 농로가 점선 결로 드러난다 — 근사가 아니라는
#: 사실을 밝힌다.
BOUNDARY_NOTE = ('붉은 점선 = 사업구역 경계(편입 필지 원형 그대로 · '
                 '판정·면적과 동일 도형)')


def _map_caption(e: dict) -> str:
    """지도 범례 한 줄 — 선종과 색이 무엇을 뜻하는지 그림 밑에 밝힌다."""
    bits = [BOUNDARY_NOTE, f'색면 = {e["name"]}']
    if e.get('area_m2'):
        bits.append(f'저촉 {_fmt_ha(e["area_m2"])}')
    return ' · '.join(bits)


def _card_info_cell(cell, e: dict, accent: str, *, show_title: bool = True) -> None:
    """
    설명 칸 — 항목·판정 / 현황 / 근거 / 해소 경로.

    `show_title=False`는 위에 소제목 띠가 이미 항목명과 판정을 적었다는
    뜻이다. 띠와 칸이 같은 말을 두 번 하면 정작 현황이 아래로 밀린다.
    """
    cell.paragraphs[0].text = ''
    if show_title:
        head = cell.paragraphs[0]
        _run(head, e['name'] + '   ', size=10, bold=True, color=INK)
        fg, _bg = STATUS_COLORS.get(e['status'], STATUS_COLORS['UNKNOWN'])
        _run(head, _sig(e['status']), size=10, bold=True, color=fg)

    fields = [('현황', e.get('fact')), ('근거', e.get('basis'))]
    # 협의 요구사항은 **자료 제공기관이 낸 조치사항 그대로**만 싣는다.
    # 해소 경로(`_route_note`)는 이 시스템이 붙인 안내라 성격이 다르므로,
    # 둘이 같은 말이 아닐 때만 따로 낸다.
    action = (e.get('action') or '').strip()
    if action and action != (e.get('route') or '').strip():
        fields.append(('협의 요구사항', action))
    fields.append(('해소 경로', e.get('route')))

    for i, (label, text) in enumerate(fields):
        p = cell.paragraphs[0] if (i == 0 and not show_title) else cell.add_paragraph()
        _run(p, label + '  ', size=8, bold=True, color=accent)
        _run(p, text or '-', size=8.5, color=INK)


def _issue_map(ctx, e: dict) -> bytes | None:
    """
    패널 지도 한 장. **서버 렌더로만 낸다 — 화면 캡처를 쓰지 않는다.**

    ⚠️ 캡처를 섞으면 크기가 어긋난다.

       화면 캡처는 브라우저 뷰포트라 가로로 길고(실측 1.9:1), 서버 렌더는
       고정 캔버스라 정사각이다. 폭만 본문 폭에 맞추고 높이를 원본 비율로
       계산하면 같은 면에 실린 지도 셋의 높이가 갈린다 — 농업진흥지역도
       1.70in vs 생태자연도 3.38in(실측). 캡처는 창 크기에 따라 비율이
       또 달라져 규격을 못 박을 수도 없다.

       그래서 패널은 `maps.PANEL_CANVAS`로 통일해 서버에서 다시 그린다.
       전폭 제약도는 화면과 그대로 맞아야 하므로 캡처를 계속 쓴다.

      1) 구역 단위 규제 레이어(`layer_geoms`) — 항목명 앞머리로 잇는다
      2) 지점 조회 항목의 도형(생태자연도·철새도래지)

    둘 다 없으면 None. 부르는 쪽이 「구역 도형 없음」을 적는다.
    """
    name = e['name']
    for layer in _env_layers(ctx):
        if layer['kind'] == 'item' and _same_item(layer['name'], name):
            got = _render_item(ctx, layer)
            if got:
                return got
    return _render_point_item(ctx, name)


def _same_item(layer: str, item: str) -> bool:
    """
    채색 사유 이름과 판정 항목명을 잇는다.

    채색은 꼬리말을 붙인다 — 「농업진흥지역도 (염도 5.5dS/m 미만)」. 정확히
    같은 문자열로 맞추면 하나도 못 잇는다.
    """
    layer, item = (layer or '').strip(), (item or '').strip()
    if not layer or not item:
        return False
    return layer.startswith(item) or item.startswith(layer)


# ======================================================================
# PART 2 — 필수 검토 항목별 공간 분석
# ======================================================================
#: 지도 폭은 상수로 적지 않는다 — 본문 폭에서 계산한다(report_style).
#: 임의의 cm를 쓰면 표와 그림의 좌우 끝선이 어긋난다.
#: 썸네일 격자의 열 수. 3열이면 A4 본문 폭에 5.2cm씩 딱 들어간다.
THUMB_COLS = 3
#: 격자에 채울 최대 칸. 인허가 실무 검토서가 6분할을 쓴다.
THUMB_MAX = 6


#: 카드 머리에 붙는 동그라미 숫자.
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩'


def gis_map_cards(doc, ctx) -> None:
    """
    발전원 프로파일이 정한 차례대로 지도 카드를 낸다.

    **번호는 여기서 매긴다.** 종전에는 카드마다 `'① 조례 이격거리 분석'`
    처럼 제목에 번호가 박혀 있었다. 그래서 발전원마다 차례가 다른데도
    (풍력은 산지, 태양광은 농지가 ②) 같은 번호가 붙었고, 카드를 하나
    끼워 넣으면 뒤 번호를 전부 손으로 밀어야 했다.
    """
    keys = [k for k in getattr(ctx.prof, 'gis_cards', ())
            if CARD_BUILDERS.get(k) is not None]
    for k in getattr(ctx.prof, 'gis_cards', ()):
        if CARD_BUILDERS.get(k) is None:
            logger.warning('알 수 없는 지도 카드: %s', k)
    for i, key in enumerate(keys):
        no = CIRCLED[i] if i < len(CIRCLED) else '·'
        try:
            CARD_BUILDERS[key](doc, ctx, no)
        except Exception:                                       # noqa: BLE001
            # 카드 하나가 깨져도 보고서 전체를 버리지 않는다.
            logger.exception('지도 카드 생성 실패: %s', key)


def _card_head(doc, title: str, headline: str, color: str = BRAND) -> None:
    """
    카드 머리 — 색 띠 안에 제목과 **한 줄 결론**을 함께 넣는다.

    인허가 실무 검토서가 지도 위에 늘 한 줄 요약을 얹는 까닭이 있다. 지도는
    읽는 데 시간이 걸리는데, 그 한 줄이 무엇을 보라는 안내가 된다.
    """
    t = doc.add_table(rows=1, cols=1)
    _no_borders(t)
    c = t.rows[0].cells[0]
    _shade(c, color)
    _run(c.paragraphs[0], title, size=11, bold=True, color='FFFFFF')
    if headline:
        _run(c.add_paragraph(), headline, size=9, color='E3EDF7')
    fix_table(t, doc)
    # 제목 띠와 지도가 붙어 있으면 답답하다 — 한 줄 띄운다.
    doc.add_paragraph()


def _put_image(doc, blob: bytes, width_emu: int) -> bool:
    """그림 한 장을 가운데 정렬로 넣는다. 폭은 EMU. 실패는 삼키고 False."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Emu
    try:
        doc.add_picture(io.BytesIO(blob), width=Emu(width_emu))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        return True
    except Exception as e:                                      # noqa: BLE001
        logger.warning('그림 삽입 실패: %s', e)
        return False


def _bullets(doc, lines: list) -> None:
    """카드 요약 불렛. 개수는 부르는 쪽이 정한다 — 카드마다 필요한 만큼 다르다."""
    for text in [x for x in lines if x]:
        p = doc.add_paragraph(style='List Bullet')
        _run(p, text, size=9, color=INK)


# ── ① 조례 이격거리 ────────────────────────────────────────────────────
def card_setback(doc, ctx, no: str = '①') -> None:
    """조례 주거·도로 이격 — 배치를 어디로 물릴지 정하는 지도."""
    r = ctx.result
    _card_head(doc, f'{no} 조례 이격거리 분석', _setback_head(ctx),
               SECTION_COLORS.get('지자체 조례', BRAND))

    img = ctx.map_image or _render_constraint(ctx)
    _log_map('① 조례 이격거리', None if ctx.map_image else ctx.outline(),
             img, '캡처' if ctx.map_image else '서버렌더')
    if img:
        _put_image(doc, img, image_width_emu(doc))

    rows = r.get('setback_rules') or []
    if rows:
        multi = len({x['sigungu'] for x in rows}) > 1
        # 표 폭을 본문 폭에 맞추고 가운데로 세운다. 지도와 표가
        # 서로 다른 폭으로 어긋나 있으면 같은 항목의 자료로 읽히지 않는다.
        _styled_table(
            doc,
            (['지자체', '이격 대상', '기준거리'] if multi else ['이격 대상', '기준거리']),
            [([x['sigungu']] if multi else []) + [x['target'], f'{x["distance_m"]:,} m']
             for x in rows],
            accent=SECTION_COLORS.get('지자체 조례', BRAND),
            widths=([2.6, 7.4, 4.0] if multi else [9.4, 4.6]),
            center=True)

    _bullets(doc, _setback_bullets(ctx))


def _setback_head(ctx) -> str:
    """
    이격 카드 한 줄 결론.

    ⚠️ **배제 면적만 보고 말하면 안 된다.** 경과규정(부칙) 검토 대상이면
    조례 이격이 배제가 아니라 **조건부**로 집계되므로 배제가 0이 된다.
    그때 "저촉 구간이 없다"고 적으면 사실과 정반대다 — 삼척 사례에서 실제로
    117ha가 조례 주거 이격에 걸렸는데도 0으로 읽혔다. 그래서 판정과 무관하게
    **조례 이격이 실제로 먹은 면적**을 사유 목록에서 직접 센다.
    """
    rows = [x for x in (ctx.result.get('by_reason') or [])
            if x['layer'].startswith('조례 ')]
    if not rows:
        return '조례 이격거리에 저촉되는 구간이 없습니다.'
    if (ctx.result.get('grandfathering') or {}).get('review_required'):
        total = sum(x['area_m2'] for x in rows)
        return ('주거·도로 이격 저촉 %s — 경과규정(부칙) 검토 대상이라 배제가 아닌 '
                '**조건부**로 집계했습니다.' % _fmt_ha(total))
    # 같은 '조례 이격'이라도 국도·지방도는 제척이고 시·군도는 등급 확인 대상이다.
    # 뭉뚱그려 '제척'이라 적으면 실제보다 크게 읽힌다.
    hard = sum(x['area_m2'] for x in rows if x['status'] == 'IMPOSSIBLE')
    soft = sum(x['area_m2'] for x in rows if x['status'] != 'IMPOSSIBLE')
    bits = []
    if hard:
        bits.append('제척 %s' % _fmt_ha(hard))
    if soft:
        bits.append('조건부 %s' % _fmt_ha(soft))
    return '주거·도로 이격 저촉 — ' + ' · '.join(bits)


#: 요약 불렛 상한. 이격 카드는 도로 등급 판단이 걸려 있어 조금 더 준다.
SETBACK_BULLET_MAX = 5


def _setback_bullets(ctx) -> list:
    """
    이격 카드 요약 — 어느 도로가 얼마나 먹었고, **그 도로가 정말 대상인지**.

    도로 이격은 이 검토에서 가장 큰 면적을 좌우하는데, 국가교통DB의
    「시·군도」에는 도로법상 군도와 농어촌도로·리도가 섞여 있어 자료만으로는
    가릴 수 없다. 그 사실과 확인 방법을 카드에 그대로 싣는다 — 계산 결과만
    보고 103ha를 제척으로 받아들이면 안 되기 때문이다.
    """
    r = ctx.result
    out = []
    roads = (r.get('geoms') or {}).get('road_detail') or []
    if roads:
        top = sorted(roads, key=lambda d: -d.get('area_m2', 0))[:3]
        out.append('도로 이격 — ' + ' · '.join(
            '%s(%s) %s 이격 %s 침범'
            % (d.get('name') or '이름 미상', d.get('rank', ''),
               f'{d.get("distance_m", 0):,}m', _fmt_ha(d.get('area_m2') or 0))
            for d in top))
    # 도로 등급 판단 근거 — roads.py가 만들어 둔 안내를 그대로 옮긴다.
    out += [n for n in (r.get('notes') or [])
            if '시·군도' in n or '「길」' in n]
    # 풍력은 어느 호기가 걸렸는지가 곧 다음 행동이다.
    if r.get('layout'):
        hit = next((m for m in ctx.merged
                    if '이격거리 조례' in m['item_name'] and m.get('hit_label')
                    and m['hit_label'] != '-'), None)
        if hit:
            out.append('이격 미달 호기 — %s' % hit['hit_label'])
    if (r.get('grandfathering') or {}).get('review_required'):
        out.append('발전사업허가일이 조례 시행일보다 앞서 **경과규정(부칙) 검토 대상**'
                   '입니다 — 적용되면 위 이격 제약이 해소될 수 있습니다.')
    return out[:SETBACK_BULLET_MAX]


def _render_constraint(ctx) -> bytes | None:
    """캡처가 없을 때 서버가 제약도를 다시 그린다."""
    g = ctx.result.get('geoms') or {}
    if ctx.site() is None:      # 지문 검증을 거친 단일 도형 — g['area']와 같다
        return None
    scr = ctx.result.get('screening') or {}
    parcels = scr.get('parcels') if not scr.get('too_wide') else None
    kw = dict(turbines=_turbine_points(ctx.result.get('layout')),
              ordinance_house=g.get('ordinance_house'),
              ordinance_road=g.get('ordinance_road'),
              ordinance_road_uncertain=g.get('ordinance_road_uncertain'),
              grandfathered=bool(g.get('ordinance_grandfathered')),
              road_detail=g.get('road_detail'))
    try:
        if parcels:
            return maps.parcel_constraint_map(ctx.outline(), parcels, **kw)
        return maps.constraint_map(ctx.outline(), g.get('blocked'),
                                   g.get('conditional'), g.get('free'), **kw)
    except Exception:                                           # noqa: BLE001
        logger.exception('제약도 생성 실패')
        return None


def _turbine_points(layout):
    if not layout:
        return None
    from . import geo
    return [geo.point_metric(a, o) for a, o in layout['turbines']]


# ── ② 토지/지형 규제 ───────────────────────────────────────────────────
def card_landuse(doc, ctx, no: str = '②') -> None:
    """태양광 — 용도지역 + 농업진흥구역. 사업 경로(염해농지)를 가르는 지도."""
    layers = _env_layers(ctx)
    zoning = [e for e in layers if e['kind'] == 'zoning']
    farm = next((e for e in layers if '농업진흥' in e['name']), None)

    head = ' · '.join(f'{z["name"]} {_fmt_ha(z["area_m2"])}' for z in zoning[:3])
    _card_head(doc, f'{no} 국토계획법 · 농지법 검토',
               head or '용도지역 정보를 조회하지 못했습니다.',
               SECTION_COLORS.get('규제/법령', BRAND))

    if zoning:
        img = ctx.env_images.get('zoning') or _render_zoning(ctx, zoning)
        if img:
            _put_image(doc, img, image_width_emu(doc))
        _styled_table(doc, ['용도지역', '면적', '비율'],
                      [[z['name'], _fmt_area(z['area_m2']),
                        _pct(z['area_m2'], ctx.total)] for z in zoning],
                      accent=SECTION_COLORS.get('규제/법령', BRAND),
                      widths=[5.0, 6.0, 3.0])

    lines = []
    if farm:
        lines.append('농업진흥구역 %s (%s) — 농지법 제28조 행위제한 대상입니다.'
                     % (_fmt_ha(farm['area_m2']), _pct(farm['area_m2'], ctx.total)))
        lines.append(_route_note('농업진흥', 'CONDITIONAL'))
    lines.append(_saline_line(ctx))
    _bullets(doc, lines)
    _public_land(doc, ctx)


PUBLIC_LAND_ITEM = '토지 소유구분(국·공유지)'


def _public_land(doc, ctx) -> None:
    """
    국·공유지 — 용도지역 아래에 소표로 붙인다.

    땅을 쓸 수 있느냐를 가르는 것은 용도지역만이 아니다. 부지에 국유지가
    섞여 있으면 **사용허가나 대부계약을 먼저 받아야** 착공이 성립한다.
    종전에는 이 항목이 PART 1 핵심 쟁점 카드에만 있어, 용도지역을 보는
    자리에서는 보이지 않았다.

    해소 경로 문구는 PART 3 인허가 순서의 「국유재산 사용허가 · 대부계약」과
    같은 말을 쓴다 — 두 곳이 다른 이름으로 부르면 같은 절차인 줄 모른다.
    """
    m = next((x for x in ctx.merged if x['item_name'] == PUBLIC_LAND_ITEM), None)
    if m is None or m['status'] == 'POSSIBLE':
        return
    doc.add_paragraph()
    _styled_table(
        doc, ['검토 항목', '판정', '현황', '근거', '해소 경로'],
        [[PUBLIC_LAND_ITEM, _sig(m['status']),
          _summarize(m.get('reason') or '', 160),
          _basis_line(m),
          '소관청 확인 → **국유재산 사용허가 또는 대부계약** '
          '(PART 3 인허가 추진 순서 참조)']],
        accent=SECTION_COLORS.get('규제/법령', BRAND), status_col=1,
        widths=[3.0, 1.8, 4.6, 3.2, 3.8])


def _saline_line(ctx) -> str:
    """염해농지 경로 — 농지라고 끝이 아니라는 사실이 카드에 있어야 한다."""
    from .providers.solar_site import SALINE_DSM
    scr = ctx.result.get('screening') or {}
    if scr.get('too_wide'):
        return ''
    farm = sum(1 for p in (scr.get('parcels') or [])
               if p.get('grade') not in ('NOT_APPLICABLE', 'IMPOSSIBLE')
               and p.get('jimok') in ('전', '답', '과수원'))
    if not farm:
        return ''
    return ('염해농지 경로 — 구역 안 농지 지목 %d필지가 대상 후보입니다. 농지면적의 '
            '90퍼센트 이상이 염도 %s dS/m 이상이면 전용이 아니라 **타용도 '
            '일시사용허가**로 사업이 가능합니다(이 시스템은 염도를 측정하지 '
            '않습니다).' % (farm, SALINE_DSM))


#: 지형·산지 카드로 묶을 항목 이름 조각.
_TERRAIN_KEYS = ('산사태', '산지구분', '경사도', '백두대간', '산림보호',
                 '재해위험', '급경사')


def _is_terrain(name: str) -> bool:
    return any(k in name for k in _TERRAIN_KEYS)


def _worst_of(items: list) -> str:
    sev = available._SEVERITY
    return max((m['status'] for m in items), key=lambda s: sev.get(s, 0),
               default='POSSIBLE')


def _terrain_head(items: list) -> str:
    if not items:
        return '산지·지형 관련 저촉 항목이 조회되지 않았습니다.'
    hit = [m for m in items if m['status'] != 'POSSIBLE']
    if not hit:
        return '산지·지형 %d개 항목 모두 해당없음입니다.' % len(items)
    return '%s — %s' % (' · '.join(m['item_name'] for m in hit[:3]),
                        _sig(_worst_of(items)))


def card_terrain(doc, ctx, no: str = '②') -> None:
    """육상풍력 — 산지·지형. 보전산지·산사태·경사도가 배치를 정한다."""
    items = [m for m in ctx.merged if _is_terrain(m['item_name'])]
    _card_head(doc, f'{no} 산지관리법 · 지형 검토', _terrain_head(items),
               SECTION_COLORS.get('산림', BRAND))

    mapped = [e for e in _env_layers(ctx) if _is_terrain(e['name'])]
    if mapped:
        _thumb_grid(doc, ctx, mapped, [])
    _tile_table(doc, items)
    _bullets(doc, [
        '경사도·산사태위험등급·산지구분은 **지점(반경) 기준으로 조회**하므로 '
        '구역 전체 도형이 없어 지도로 내지 못합니다 — 판정만 싣습니다.',
        _route_note('산지', _worst_of(items)) if items else '',
    ])


# ── ③ 환경성 평가 협의지침 ─────────────────────────────────────────────
#: 환경성 면에 반드시 자리를 주는 항목. 지도가 없어도 판정만이라도 낸다 —
#: 협의지침이 명시한 입지회피 검토사항이라 빠지면 안 된다.
ENV_ALWAYS = ('생태자연도', '철새도래지', '백두대간', '생태·경관보전지역',
              '습지보호', '야생동식물보호')


#: 이 판정을 받은 환경성 항목은 **항목당 한 줄**로 펼친다. 협의 상대와
#: 요구사항이 항목마다 다르므로, 묶어 놓으면 협의 자료로 떼어 낼 수 없다.
ENV_EXPAND = ('IMPOSSIBLE', 'CONDITIONAL')


def card_environment(doc, ctx, no: str = '③') -> None:
    """
    환경성 평가 협의지침 — **조건부는 펼치고, 해당없음은 접는다.**

    종전에는 6칸 격자에 다 밀어넣었다. 그러면 협의가 필요한 항목(농업진흥
    지역도·생태자연도·철새도래지)이 2.05in짜리 그림으로 줄어 규제 경계가
    안 보였고, 해당없음 항목이 똑같은 크기로 자리를 차지해 무엇이 문제인지
    가려지지 않았다. 지면은 **해야 할 일이 있는 항목**에 준다.
    """
    entries = _env_entries(ctx)
    hot = [e for e in entries if e['status'] in ENV_EXPAND]
    rest = [e for e in entries if e['status'] not in ENV_EXPAND]

    _card_head(doc, f'{no} 환경성 평가 협의지침 검토', _env_headline(entries),
               SECTION_COLORS.get('환경', BRAND))
    if not entries:
        doc.add_paragraph('사업구역과 겹치는 환경성 규제가 조회되지 않았습니다.')
        return

    accent = SECTION_COLORS.get('환경', BRAND)
    for i, e in enumerate(hot, start=1):
        # 항목마다 소제목 띠를 얹는다. 종전에는 같은 모양의 2열 표가 연달아
        # 이어져 어디서 한 항목이 끝나는지 읽히지 않았다.
        _sub_band(doc, '%s-%d  %s' % (no, i, e['name']),
                  _sig(e['status']), accent)
        _map_card(doc, ctx, e, accent, show_title=False)
        if i < len(hot):
            # 항목 사이를 종전보다 넉넉히 띄운다.
            doc.add_paragraph()
    _env_rest(doc, rest, accent)
    _note(doc,
          '※ 지도의 붉은 점선이 사업구역 경계입니다. 「협의 요구사항」은 판정 '
          '단계에서 각 자료 제공기관이 낸 조치사항만 옮긴 것이며, 원문으로 '
          '확인하지 못한 협의지침 내용은 싣지 않았습니다. 협의 시에는 해당 '
          '기관 자료로 재확인하십시오.')


def _env_entries(ctx) -> list[dict]:
    """
    환경성 항목을 **한 목록으로** 모은다.

    자료가 두 갈래로 온다 — 구역 전체를 조회해 도형이 남은 규제 레이어와,
    지점(반경)으로만 조회되는 판정 항목(생태자연도·철새도래지 등). 종전에는
    이 둘을 `mapped`/`tiles`로 따로 그려 같은 항목이 두 번 나오거나 판정과
    지도가 어긋났다. 여기서 항목명으로 이어 한 줄로 만든다.
    """
    layers = [e for e in _env_layers(ctx)
              if e['kind'] == 'item' and not _is_terrain(e['name'])]
    area_of = _area_by_name(ctx.result)
    hits = _parcel_hits(ctx)

    # 안전/문화재 분류는 이 면의 소관이 아니다 — ④ 문화재·국가유산 카드가
    # 다룬다. 구역 전체를 덮는 광역 공역(군작전구역·접근관제구역)이 '구역
    # 전체 조건'으로 env_layers에 실려 오면서 환경성 면에 군사 항목이 끼는
    # 것을 여기서 막는다. 이름이 아니라 **판정 항목의 분류**로 가른다.
    def env_scope(m) -> bool:
        return (m.get('category') or '') != '안전/문화재'

    items = [m for m in ctx.merged
             if env_scope(m)
             and (any(k in m['item_name'] for k in ENV_ALWAYS)
                  or any(_same_item(l['name'], m['item_name']) for l in layers))]

    out, taken = [], set()
    # 분류로 걸러진 판정 항목과 이어지는 레이어도 잔여로 남기지 않는다 —
    # 잔여 루프에 떨어지면 분류 필터를 우회해 다시 실린다.
    for m in ctx.merged:
        if env_scope(m):
            continue
        for l in layers:
            if _same_item(l['name'], m['item_name']):
                taken.add(id(l))
    for m in items:
        lay = next((l for l in layers if _same_item(l['name'], m['item_name'])), None)
        if lay is not None:
            taken.add(id(lay))
        out.append({
            'name': m['item_name'],
            'status': m['status'],
            'area_m2': area_of(m['item_name']) or (lay or {}).get('area_m2') or 0.0,
            'fact': _fact_line(m, area_of, hits=hits),
            'basis': _basis_line(m),
            'route': _route_note(m['item_name'], m['status'],
                                 m.get('action_required') or ''),
            'action': (m.get('action_required') or '').strip(),
        })
    # 판정 항목과 못 이어진 규제 레이어 — 도형은 있으니 버리지 않는다.
    for lay in layers:
        if id(lay) in taken:
            continue
        out.append({
            'name': lay['name'], 'status': lay['status'] or 'CONDITIONAL',
            'area_m2': lay['area_m2'],
            'fact': _fmt_ha(lay['area_m2']),
            'basis': '◇ 확인 필요 — 근거 법령·조문이 확인되지 않았습니다.',
            'route': _route_note(lay['name'], lay['status'] or 'CONDITIONAL'),
            'action': '',
        })
    sev = available._SEVERITY
    out.sort(key=lambda e: (-sev.get(e['status'], 0), -e['area_m2']))
    return out


def _env_headline(entries: list) -> str:
    hit = [e['name'] for e in entries if e['status'] in ENV_EXPAND]
    if not hit:
        return '환경성 입지회피 항목 %d개 모두 해당없음·확인 필요입니다.' % len(entries)
    return '협의 대상 %d개 — %s' % (len(hit), ' · '.join(hit[:3]))


def _env_rest(doc, rest: list, accent: str) -> None:
    """
    펼치지 않는 항목 — ○ 해당없음은 **한 줄로 묶고**, ◇ 확인 필요는 남긴다.

    둘을 같이 묶으면 안 된다. 해당없음은 확인이 끝난 것이고, 확인 필요는
    아직 아무도 보지 않은 것이다. 한 줄에 섞으면 그 차이가 사라진다.
    """
    if not rest:
        return
    ok = [e for e in rest if e['status'] == 'POSSIBLE']
    unknown = [e for e in rest if e['status'] != 'POSSIBLE']

    rows = []
    if ok:
        rows.append(['%d개 항목' % len(ok), _sig('POSSIBLE'),
                     ' · '.join(e['name'] for e in ok)])
    for e in unknown:
        rows.append([e['name'], _sig(e['status']), e['fact']])
    _styled_table(doc, ['검토 항목', '판정', '현황'], rows,
                  accent=accent, status_col=1, widths=[4.0, 2.0, 10.4])


def _thumb_grid(doc, ctx, mapped: list, tiles: list) -> None:
    """
    썸네일 격자 — docx 표를 격자로 쓴다.

    matplotlib은 한 PNG에 지도 하나만 그린다(figure가 고정 정사각형). 지도를
    격자로 붙이려고 그 배관을 뜯는 대신, **표 3열**에 작은 그림을 한 칸씩
    넣는다. 인허가 검토서의 6분할 면과 같은 모양이 나오고 기존 지도 함수를
    그대로 쓴다.

    칸은 두 종류다 — 도형이 있으면 지도, 없으면 판정 타일.
    """
    cells = ([('map', e) for e in mapped] + [('tile', m) for m in tiles])[:THUMB_MAX]
    if not cells:
        return
    rows = (len(cells) + THUMB_COLS - 1) // THUMB_COLS
    t = doc.add_table(rows=rows, cols=THUMB_COLS)
    _no_borders(t)
    # 그림 폭은 표를 세운 뒤 **그 열 폭에서** 계산한다. 칸 안에서 doc을 다시
    # 뒤지면 스코프가 어긋나 그림이 조용히 빠진다(종전 `_thumb_tile`의 버그).
    thumb_emu = image_width_emu(doc, THUMB_COLS)
    for i, (kind, obj) in enumerate(cells):
        cell = t.rows[i // THUMB_COLS].cells[i % THUMB_COLS]
        cell.paragraphs[0].text = ''
        if kind == 'map':
            _thumb_map(cell, ctx, obj, thumb_emu)
        else:
            _thumb_tile(cell, obj, ctx, thumb_emu)
    fix_table(t, doc)
    doc.add_paragraph()


def _thumb_map(cell, ctx, e: dict, width_emu: int) -> None:
    """지도 칸 — 제목 · 작은 지도 · 판정 캡션."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Emu
    _run(cell.paragraphs[0], e['name'], size=9, bold=True, color=INK)
    img = ctx.env_images.get(e['name']) or _render_item(ctx, e)
    if img:
        try:
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(io.BytesIO(img), width=Emu(width_emu))
        except Exception as ex:                                 # noqa: BLE001
            logger.warning('썸네일 삽입 실패(%s): %s', e['name'], ex)
    _run(cell.add_paragraph(),
         '%s · %s' % (_sig(e['status'] or 'CONDITIONAL'), _fmt_ha(e['area_m2'])),
         size=8, color=MUTED)


#: 지점 조회 항목의 도형을 지도로 낼 수 있는 것들.
#:
#: 생태자연도·철새도래지는 구역 단위 V-World 레이어가 아니라 지점(반경)
#: provider라 `layer_geoms`에 안 남는다. 그렇다고 판정 글자만 내면 "어디가
#: 어떤 상태인지"를 못 본다 — 협의 자료로 못 쓴다. 그래서 판정 때 쓴 자료를
#: 도형으로 한 번 더 받아 그린다(조회는 캐시가 받쳐 준다).
def _point_item_geoms(ctx, name: str):
    """→ ([shapely in EPSG:5179], 색) 또는 (None, None)."""
    q = _site_query(ctx)
    if q is None:
        return None, None
    try:
        if '생태자연도' in name:
            from .providers.econature import grade_geoms
            rows = grade_geoms(q)
            # 가장 보전 등급이 높은(숫자가 작은) 것만 그린다 — 다 겹쳐 칠하면
            # 어느 구역이 문제인지 되레 안 보인다.
            if not rows:
                return None, None
            worst = min(g for g, _ in rows)
            return [geom for g, geom in rows if g == worst], _ECO_COLOR.get(worst, '#e8a33d')
        if '철새도래지' in name:
            from .providers.birds import habitat_geoms
            geoms = habitat_geoms(q)
            return (geoms or None), '#7C5BB5'
    except Exception:                                           # noqa: BLE001
        logger.exception('%s 도형 조회 실패', name)
    return None, None


#: 생태자연도 등급별 색 — 1등급이 가장 엄하다.
_ECO_COLOR = {1: '#c0392b', 2: '#e8a33d', 3: '#3C8C3C', 9: '#7b1fa2'}


def _site_query(ctx):
    """
    판정에 쓴 것과 **같은 검토 도형**으로 SiteQuery를 다시 만든다.

    도형은 shapely 그대로 넘긴다(area_geom). 종전에는 링 목록으로 풀어
    첫 링만 되감았는데, 사업구역이 필지 합(멀티폴리곤)이면 **첫 조각의
    바깥 링만 남고 나머지 조각과 구멍이 전부 사라진다** — 판정 도형이
    소리 없이 좁아지는 손실이었다.
    """
    from .providers.base import SiteQuery
    ev = ctx.evals[0] if ctx.evals else None
    if not ev:
        return None
    site = ctx.site()
    if site is None:
        return None
    try:
        return SiteQuery(lat=ev['lat'], lng=ev['lng'], radius_m=100,
                         address='', area_geom=site)
    except Exception:                                           # noqa: BLE001
        return None


def _thumb_tile(cell, m: dict, ctx=None, width_emu: int = 0) -> None:
    """
    판정 칸 — **조건부·불가면 지도를 먼저 시도한다.**

    "생태자연도 조건부"라는 글자만으로는 부지의 어디가 걸렸는지 알 수 없어
    협의 자료로 못 쓴다. 도형을 구할 수 있으면 농업진흥지역도처럼 지도를
    싣고, 구하지 못했을 때만 색 타일로 대신한다.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Emu

    fg, bg = STATUS_COLORS.get(m['status'], STATUS_COLORS['UNKNOWN'])
    _run(cell.paragraphs[0], m['item_name'], size=9, bold=True, color=INK)

    img = None
    if ctx is not None and width_emu and m['status'] in ('IMPOSSIBLE', 'CONDITIONAL'):
        img = (ctx.env_images.get(m['item_name'])
               or _render_point_item(ctx, m['item_name']))
    if img:
        try:
            p = cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run().add_picture(io.BytesIO(img), width=Emu(width_emu))
        except Exception as e:                                  # noqa: BLE001
            logger.warning('썸네일 삽입 실패(%s): %s', m['item_name'], e)
            img = None
    if not img:
        # 지도를 못 냈을 때만 색을 깐다 — 지도가 있으면 색이 그림을 가린다.
        _shade(cell, bg)
    _run(cell.add_paragraph(), _sig(m['status']), size=9, bold=True, color=fg)
    _run(cell.add_paragraph(), _summarize(m.get('reason') or '', 64),
         size=7.5, color=INK)


def _render_point_item(ctx, name: str) -> bytes | None:
    """지점 조회 항목의 도형을 사업구역 위에 그린다."""
    area = ctx.outline()
    if area is None:
        return None
    geoms, color = _point_item_geoms(ctx, name)
    if not geoms:
        return None
    try:
        png = maps.item_map(area, geoms, name, color or '#e8a33d')
        _log_map(name, area, png, '서버렌더')
        return png
    except Exception:                                           # noqa: BLE001
        logger.exception('%s 지도 생성 실패', name)
        return None


def _tile_table(doc, items: list) -> None:
    """지도가 없는 항목을 표로 — 격자에 다 못 담을 때."""
    rows = [m for m in items if m['status'] != 'POSSIBLE']
    if not rows:
        return
    _styled_table(doc, ['검토 항목', '판정', '현황'],
                  [[m['item_name'], _sig(m['status']),
                    _summarize(m.get('reason') or '', 80)] for m in rows],
                  accent=SECTION_COLORS.get('산림', BRAND), status_col=1,
                  widths=[4.4, 2.0, 8.0])


def _env_layers(ctx) -> list:
    """구역 단위로 도형이 남은 항목 — 지도로 낼 수 있는 것의 전부다."""
    try:
        return available.env_layers(ctx.result)
    except Exception:                                           # noqa: BLE001
        logger.exception('환경성 레이어 목록 생성 실패')
        return []


def _render_zoning(ctx, zoning: list) -> bytes | None:
    area = ctx.outline()
    if area is None:
        return None
    try:
        return maps.zoning_map(area, {z['name']: z['geom'] for z in zoning})
    except Exception:                                           # noqa: BLE001
        logger.exception('용도지역 지도 생성 실패')
        return None


#: 항목 색 — matplotlib 색이라 '#'가 붙는다(docx RGBColor와 반대).
_ENV_MAP_COLOR = {'IMPOSSIBLE': '#d9363e', 'CONDITIONAL': '#e8a33d'}


def _render_item(ctx, e: dict) -> bytes | None:
    area = ctx.outline()
    if area is None or e.get('geom') is None:
        return None
    try:
        png = maps.item_map(area, [e['geom']], e['name'],
                            _ENV_MAP_COLOR.get(e['status'], '#e8a33d'))
        _log_map(e['name'], area, png, '서버렌더')
        return png
    except Exception:                                           # noqa: BLE001
        logger.exception('%s 지도 생성 실패', e['name'])
        return None


# ── ④ 문화재 · 국가유산 ────────────────────────────────────────────────
#: 이 카드가 묶는 판정 항목. **차례가 표의 차례다.**
#:
#: 셋은 같은 사안의 세 얼굴이다 — 지정된 구역인가(보호구역), 그 둘레의
#: 경관을 규율받는가(역사문화환경 보존지역), 땅속에 무엇이 있는가(조사구역).
#: 종전에는 PART 1 핵심 쟁점 다섯 칸 중 **셋을 이 항목들이 차지해** 정작
#: 다른 쟁점이 밀려났고, 정작 PART 2에는 국가유산 면이 아예 없었다.
HERITAGE_ITEMS = (
    '국가유산 지정/보호구역',
    '국가유산·역사문화환경 보존지역',
    '국가유산조사구역',
)

#: PART 3 인허가 순서에서 이 카드가 끌어올 절차. 판정·근거를 그쪽에서
#: 가져와 두 곳이 어긋나지 않게 한다.
#:
#: ⚠️ 절차 이름이 바뀔 수 있으므로 **완전일치로 찾지 않는다.** 실제로
#: 2024-02-13 개정(사업시행자 지표조사 의무가 매장유산법 구 제6조에서
#: 「국가유산영향진단법」 제9조로 이관)을 반영해 절차명을 「국가유산 영향진단
#: (매장유산 지표조사 포함)」으로 고쳤는데, 완전일치였다면 그 순간 이 카드가
#: 조용히 '절차를 찾지 못했습니다'로 바뀌었을 것이다.
SURVEY_STEP_KEYS = ('국가유산 영향진단', '매장유산 지표조사')


def _find_survey_step(ctx):
    for key in SURVEY_STEP_KEYS:
        step = next((x for x in _all_steps(ctx) if key in (x.get('name') or '')), None)
        if step is not None:
            return step
    return None


def card_heritage(doc, ctx, no: str = '④') -> None:
    """
    문화재 · 국가유산 — 위치와 거리를 지도에 올린다.

    "1.77km 지점입니다"라는 문장만으로는 어느 방향에 무엇이 있는지 알 수
    없어 현상변경 협의를 시작할 수 없다. 전력계통 카드가 변전소를 점으로
    찍고 거리를 붙이듯, 국가유산도 같은 방식으로 낸다.
    """
    rows = [m for m in ctx.merged if m['item_name'] in HERITAGE_ITEMS]
    _card_head(doc, f'{no} 문화재 · 국가유산 검토', _heritage_head(ctx, rows),
               SECTION_COLORS.get('안전/문화재', BRAND))

    area = ctx.outline()
    if area is not None:
        try:
            png = maps.heritage_map(area, _heritage_points(ctx),
                                    _survey_geoms(ctx))
            _log_map('④ 문화재·국가유산', area, png, '서버렌더')
            _put_image(doc, png, image_width_emu(doc))
            _run(doc.add_paragraph(),
                 BOUNDARY_NOTE + ' · ▲ = 지정·등록 국가유산(명칭·종별·'
                 '직선거리) · 색면 = 국가유산조사구역',
                 size=7.5, color=MUTED)
        except Exception:                                       # noqa: BLE001
            logger.exception('국가유산 위치도 생성 실패')

    _styled_table(
        doc, ['검토 항목', '판정', '현황', '근거 법령·조문', '협의·해소 경로'],
        [[m['item_name'], _sig(m['status']),
          _summarize(m.get('reason') or '', 150), _basis_line(m),
          _route_note(m['item_name'], m['status'],
                      m.get('action_required') or '')]
         for m in rows] + [_survey_row(ctx)],
        accent=SECTION_COLORS.get('안전/문화재', BRAND), status_col=1,
        widths=[3.4, 1.8, 4.4, 3.0, 3.8])


def _heritage_head(ctx, rows: list) -> str:
    hit = [m['item_name'] for m in rows if m['status'] != 'POSSIBLE']
    pts = _heritage_points(ctx)
    if pts:
        near = pts[0]
        return '최근접 %s(%s) %s — 협의 대상 %d개 항목' % (
            near.get('name') or '명칭 미상', near.get('kind') or '종별 미상',
            _km(near.get('distance_m')), len(hit))
    return ('협의 대상 — ' + ' · '.join(hit)) if hit         else '조회 범위 내 지정·등록 국가유산이 확인되지 않았습니다.'


def _heritage_points(ctx) -> list:
    """지도에 찍을 국가유산 — 판정이 이미 받아 둔 목록을 그대로 쓴다."""
    for e in ctx.evals:
        res = e.get('result')
        for it in getattr(res, 'analysis_items', []) or []:
            if it.item_name == '국가유산·역사문화환경 보존지역':
                items = (it.raw or {}).get('heritages') or []
                out = [h for h in items if h.get('lat') and h.get('lng')]
                out.sort(key=lambda h: h.get('distance_m') or 0)
                return out
    return []


def _survey_geoms(ctx) -> list:
    """사업구역에 걸치는 국가유산조사구역 도형."""
    layer_geoms = ((ctx.result.get('geoms') or {}).get('layer_geoms')) or {}
    return [g for name, g in layer_geoms.items()
            if '국가유산조사구역' in name and g is not None]


def _survey_row(ctx) -> list:
    """
    매장유산 지표조사 대상 여부 — **PART 3의 같은 절차에서 끌어온다.**

    종전에는 인허가 순서표에는 있고 PART 2에는 없어, 지표조사가 필요한지를
    보려면 문서 뒤쪽 표를 찾아야 했다. 두 곳이 따로 판정하면 어긋나므로
    로드맵이 낸 판정과 사유를 그대로 옮긴다.
    """
    step = _find_survey_step(ctx)
    if step is None:
        return ['국가유산 영향진단 대상 여부 (사업면적 기준)', _sig('UNKNOWN'),
                '인허가 절차 목록에서 해당 절차를 찾지 못했습니다.',
                '◇ 확인 필요 — 근거 법령·조문이 확인되지 않았습니다.',
                '국가유산청에 대상 여부를 확인하십시오.']
    status = 'CONDITIONAL' if step.get('applicable') else 'UNKNOWN'
    return ['국가유산 영향진단 대상 여부 (사업면적 기준)', _sig(status),
            _summarize(step.get('applicability_reason') or '', 150),
            _basis_line(step),
            '국가유산청 협의 → 매장유산 조사기관 지표조사 '
            '(PART 3 인허가 추진 순서 참조)']


# ── ④ 전력계통 연계 ────────────────────────────────────────────────────
GRID_ITEM = '전력계통 연계(변전소·송전선로)'
#: 연계 대상으로 보는 최소 전압(한전 154kV 이상 계통).
GRID_MIN_VOLT = 154_000


def _item_of(res, name: str):
    return next((i for i in res.analysis_items if i.item_name == name), None)


def _grid_subs(item) -> list:
    """154kV 이상·이름이 확인된 변전소만. 명칭 미상은 협의 상대가 안 된다."""
    if item is None:
        return []
    return [s for s in ((item.raw or {}).get('substations') or [])
            if (s.get('voltage') or 0) >= GRID_MIN_VOLT
            and s.get('name') != '(명칭 미상)']


def _kw(v) -> str:
    return f'{int(v):,} kW' if v is not None else '-'


def _grid_head(item) -> str:
    if item is None:
        return '전력계통 판정 결과가 없습니다.'
    subs = _grid_subs(item)
    if not subs:
        return '반경 30km 내 154kV 이상 변전소가 조회되지 않았습니다.'
    s = subs[0]
    return '최근접 %s (%dkV) 직선 %.1fkm — %s' % (
        s.get('name') or '명칭 미상', (s.get('voltage') or 0) // 1000,
        (s.get('distance_m') or 0) / 1000, _sig(item.status.value))


def card_grid(doc, ctx, no: str = '⑤') -> None:
    """최근접 154kV 변전소까지의 경로 — 선로 포설 거리와 여유용량."""
    item = next((it for it in (_item_of(e['result'], GRID_ITEM)
                               for e in ctx.evals) if it is not None), None)
    _card_head(doc, f'{no} 전력계통 연계 인프라', _grid_head(item),
               SECTION_COLORS.get('인프라', BRAND))
    if item is None:
        doc.add_paragraph('전력계통 판정 결과가 없습니다.')
        return

    subs = _grid_subs(item)
    road_m = (item.raw or {}).get('road_distance_m')
    area = ctx.outline()
    if area is not None:
        try:
            png = maps.grid_route_map(area, subs, road_m)
            _log_map('⑤ 전력계통', area, png, '서버렌더')
            _put_image(doc, png, image_width_emu(doc))
        except Exception:                                       # noqa: BLE001
            logger.exception('계통 경로도 생성 실패')

    if subs:
        # 도로망 경로를 **앞 열**에 둔다. 선로 포설비·공사기간을 가르는 것은
        # 직선거리가 아니라 실제로 케이블을 끌고 갈 길이다. 직선거리는
        # 참고로 뒤에 둔다.
        _styled_table(
            doc, ['변전소', '전압', '도로망 경로', '직선거리', '변전소 여유', '선로 여유'],
            [[s.get('name') or '-', '%dkV' % ((s.get('voltage') or 0) // 1000),
              _km(road_m) if i == 0 else '-',
              _km(s.get('distance_m')),
              _kw(s.get('margin_substation_kw')), _kw(s.get('margin_line_kw'))]
             for i, s in enumerate(subs[:3])],
            accent=SECTION_COLORS.get('인프라', BRAND),
            widths=[4.2, 1.8, 2.8, 2.4, 3.1, 3.1], center=True)

    ratio = ''
    if road_m and subs and subs[0].get('distance_m'):
        ratio = ' (직선 대비 %.1f배)' % (road_m / max(subs[0]['distance_m'], 1))
    _bullets(doc, [
        ('선로 포설 길이는 **도로망 경로 %s%s** 를 기준으로 보십시오 — 실제 '
         '케이블은 도로를 따라 포설되므로 직선거리로는 공사비가 과소평가됩니다.'
         % (_km(road_m), ratio)) if road_m else
        '도로망 경로 거리를 산출하지 못했습니다 — 직선거리만 참고하십시오.',
        '경과지는 한전 협의로 확정되며, 지장물·하천 횡단 여부에 따라 더 길어질 수 있습니다.',
        '여유용량은 조회 시점 스냅샷이며 다른 사업의 선점으로 변동합니다.',
    ])


def _km(m) -> str:
    return '%.2f km' % (m / 1000) if m else '-'


#: PART 2 카드 열쇠 → 렌더 함수. `energy.EnergyProfile.gis_cards`가 차례를 쥔다.
CARD_BUILDERS = {
    'setback': card_setback,
    'landuse': card_landuse,
    'terrain': card_terrain,
    'environment': card_environment,
    'heritage': card_heritage,
    'grid': card_grid,
}


# ======================================================================
# PART 3 — 리스크 매트릭스 & 인허가 마일스톤
# ======================================================================
#: 사업 영향도 3단. 판정 감점 + 난이도 감점을 접어 만든다 — 숫자를 그대로
#: 내면 '46점'처럼 절대 기준으로 오인되므로 상/중/하로만 말한다.
IMPACT_HIGH = 20
IMPACT_MID = 10


def _impact(status: str, difficulty: str) -> str:
    """사업 영향도 — 판정과 난이도를 함께 본다. 점수는 노출하지 않는다."""
    from .schemas import DIFFICULTY_PENALTY, STATUS_PENALTY
    score = STATUS_PENALTY.get(status, 0) + DIFFICULTY_PENALTY.get(difficulty, 0)
    if status == 'IMPOSSIBLE' or score >= IMPACT_HIGH:
        return '상'
    return '중' if score >= IMPACT_MID else '하'


#: 영향도별 (글자색, 배경색) — 표를 훑을 때 색으로 먼저 읽히게 한다.
IMPACT_COLORS = {
    '상': STATUS_COLORS['IMPOSSIBLE'],
    '중': STATUS_COLORS['CONDITIONAL'],
    '하': STATUS_COLORS['POSSIBLE'],
}


def risk_matrix_table(doc, ctx) -> None:
    """
    리스크 해소 경로 매트릭스.

    판정만 있는 표는 "그래서 어떻게"에 답하지 못한다. 항목마다 **무엇이
    걸렸고 · 얼마나 아프고 · 무엇을 하면 되는지**를 한 줄에 세워야 대관
    협의 계획이 선다.
    """
    rows = sorted((m for m in ctx.merged if m['status'] != 'POSSIBLE'),
                  key=lambda m: _RISK_ORDER.get(_impact(m['status'],
                                                        m.get('difficulty')), 9))
    if not rows:
        _callout(doc, '해소가 필요한 리스크가 없습니다',
                 '검토한 항목이 모두 해당없음입니다.', tone='POSSIBLE')
        return

    doc.add_heading('리스크 해소 경로 매트릭스', level=2)
    _styled_table(
        doc, ['구분', '리스크 항목', '현황 · 규제 근거', '영향도', '해소 전략 · 대관 협의'],
        [[m.get('category') or '기타', m['item_name'], _basis(m),
          _impact(m['status'], m.get('difficulty')),
          _route_note(m['item_name'], m['status'], m.get('action_required') or '')]
         for m in rows],
        accent='B5426E', widths=[1.9, 3.4, 4.2, 1.3, 6.6])
    _note(doc, '※ 영향도는 판정(불가·조건부·확인 필요)과 해소 난이도를 함께 접은 '
               '값입니다. 상대 지표이며 법적 판단이 아닙니다.')


_RISK_ORDER = {'상': 0, '중': 1, '하': 2}


def _basis(m: dict, limit: int = 64) -> str:
    """현황 한 줄 + 근거 규정. 근거가 없으면 사유만."""
    head = _summarize(m.get('reason') or '', limit)
    law = ' '.join(x for x in (m.get('law'), m.get('article')) if x)
    return f'{head}\n▸ {law}' if law else (head or '-')


def milestone_checklist(doc, ctx) -> None:
    """
    선행 인허가 추진 순서.

    달력 일정은 만들지 않는다 — 데이터에 없다. 대신 **차례·소관·법정
    처리기간·선행조건**을 싣는다. 그것만으로도 무엇을 먼저 착수해야 하는지
    정해진다.
    """
    steps = _roadmap_rows(ctx)
    if not steps:
        return
    doc.add_heading('선행 인허가 추진 순서', level=2)
    _styled_table(
        doc, ['단계', '절차', '소관', '법정처리기간', '선행 조건', '비고'],
        [[s.get('phase') or '-', s.get('name') or '-', s.get('authority') or '-',
          s.get('statutory_label') or '◇ 확인 필요',
          ' · '.join(s.get('depends_on') or []) or '-',
          s.get('_remark') or '-']
         for s in steps],
        accent='C07A1E', widths=[1.5, 4.0, 3.0, 2.2, 3.5, 3.2])
    _note(doc, '※ 법정처리기간은 접수 후 처분까지의 법정 기한이며 보완 기간은 '
               '빠져 있습니다. 실제 소요는 이보다 깁니다. '
               '「법정기간 없음(협의 소요)」은 조문에 기한 규정이 없다는 뜻이고, '
               '「◇ 확인 필요」는 아직 원문으로 확인하지 못했다는 뜻입니다 — '
               '두 가지는 다릅니다.')
    _landuse_route_note(doc, ctx)
    _pending_steps(doc, ctx)


#: 국토계획법 경로를 가르는 두 절차 — 서로 **택일**이다.
#: (engine.derive_site_flags가 내는 플래그. permits.FLAG_LABEL에 라벨이 있다)
_ROUTE_URBAN = 'URBAN_AREA'
_ROUTE_NON_URBAN = 'NON_URBAN_AREA'

#: 택일 경로에 붙일 비고. 어느 쪽을 밟을지는 **사업자가 정할 일**이라
#: 시스템이 단정하지 않는다 — 다만 정해야 한다는 사실은 표에 남긴다.
_ROUTE_REMARK = '⚑ 택일 — 사업별 인허가\n추진 방향 검토 필요'

#: 도시계획시설 경로를 밟으면 **실시계획 인가·고시로 의제되는** 인허가.
#: 국토계획법 제92조제1항 각 호를 원문에서 뽑았다(2026-08 대조).
#:
#: 이 표시가 있어야 경로 선택이 무엇을 바꾸는지 표에서 바로 읽힌다 —
#: 도시계획시설을 택하면 아래 절차들이 개별 인허가가 아니라 실시계획
#: 인가 하나로 갈음되므로, 절차 수와 일정이 통째로 달라진다.
_DEEMED_BY_LAW = {
    '농지법': '8호',
    '도로법': '9호',
    '장사 등에 관한 법률': '10호',
    '사방사업법': '12호',
    '산지관리법': '13호',
    '초지법': '23호',
    '하천법': '26호',
}


def _deemed_remark(step: dict, certain: bool) -> str:
    """이 절차가 제92조로 의제되는가 → 비고 문구. 아니면 빈 문자열."""
    ho = _DEEMED_BY_LAW.get((step.get('law') or '').strip())
    if not ho:
        return ''
    return ('실시계획 인가로 의제됨\n(법 제92조①%s)' % ho if certain else
            '도시계획시설 경로 선택 시\n실시계획 인가로 의제\n(법 제92조①%s)' % ho)


def _route_pair(ctx) -> tuple:
    """국토계획법 택일 경로 짝 → (도시지역용, 도시지역 밖용). 없으면 (None, None)."""
    m = {s.get('conditional_on'): s for s in _all_steps(ctx)
         if s.get('conditional_on') in (_ROUTE_URBAN, _ROUTE_NON_URBAN)}
    return m.get(_ROUTE_URBAN), m.get(_ROUTE_NON_URBAN)


def _roadmap_rows(ctx) -> list:
    """
    순서표에 실을 줄 — 해당하는 절차 + **택일 경로의 다른 쪽**.

    종전에는 해당하는 절차만 실어(`_roadmap`), 도시지역 밖에서는 개발행위허가만
    남고 도시계획시설 경로가 표에서 사라졌다. 그런데 시행령 제35조제1항제2호
    나목은 결정 없이 설치"할 수 있다"는 **면제**이지 금지가 아니라, 도시지역
    밖에서도 도시계획시설 경로를 고를 수 있다 — 실제로 그렇게 추진하는 사업이
    있다(삼척 천봉풍력: 22기 전부 농림지역인데 도시관리계획 결정고시).

    어느 쪽을 밟을지는 수용권·인허가 의제가 필요한지에 따라 **사업자가**
    정하므로, 둘을 나란히 싣고 비고에 검토가 필요하다는 사실을 남긴다.

    ⚠️ 도시지역이면 개발행위허가는 법 제56조제1항 단서로 **불가**다. 그때는
       고를 것이 없으므로 짝을 끌어올리지 않는다.
    """
    # ⚠️ `_all_steps`는 부를 때마다 **새 dict**를 만든다. 표에 쓸 목록과
    #    경로 짝을 따로 부르면 서로 다른 객체를 쥐게 되어, 짝에 붙인 비고가
    #    정작 표에는 나타나지 않는다(실측으로 잡은 실수다). 한 번만 읽는다.
    all_steps = _all_steps(ctx)
    rows = [s for s in all_steps if s.get('applicable')]
    pair = {s.get('conditional_on'): s for s in all_steps
            if s.get('conditional_on') in (_ROUTE_URBAN, _ROUTE_NON_URBAN)}
    urban, non_urban = pair.get(_ROUTE_URBAN), pair.get(_ROUTE_NON_URBAN)
    if not urban or not non_urban:
        return rows                 # 이 발전원은 아직 경로 분기를 두지 않았다

    in_urban = bool(urban.get('applicable'))
    if in_urban:                    # 도시지역 — 선택의 여지가 없다
        urban['_remark'] = '도시지역 — 이 경로가 의무\n(개발행위허가 불가)'
        out = rows
    else:
        urban['_remark'] = _ROUTE_REMARK
        non_urban['_remark'] = _ROUTE_REMARK
        out = sorted(rows + [urban], key=lambda d: d.get('order') or 0)

    # 경로 선택이 무엇을 바꾸는지 각 줄에 표시한다. 도시지역이면 이미 그
    # 경로가 확정이라 '의제됨'으로, 택일이면 '선택 시 의제'로 적는다.
    for s in out:
        if s.get('_remark'):
            continue                # 경로 줄 자체는 위에서 붙였다
        mark = _deemed_remark(s, certain=in_urban)
        if mark:
            s['_remark'] = mark
    return out


#: 국토계획법 경로를 가르는 두 절차 — 서로 **택일**이다.
#: (engine.derive_site_flags가 내는 플래그. permits.FLAG_LABEL에 라벨이 있다)
_ROUTE_URBAN = 'URBAN_AREA'
_ROUTE_NON_URBAN = 'NON_URBAN_AREA'


def _landuse_route_note(doc, ctx) -> None:
    """
    국토계획법 인허가 **경로**를 밝힌다.

    위 표는 해당하는 절차만 싣기 때문에, 택일인 두 경로 중 선택되지 않은
    쪽은 표에서도 「대상 여부 미확정」에서도 빠진다(사유가 '확인 필요'가
    아니라 '해당하지 않음'이라서). 그 결과 개발행위허가만 덩그러니 남아,
    **애초에 갈림길이 있었다는 사실 자체가 문서에서 사라진다.**

    갈림길은 결론이 아니라 전제다 — 부지를 조금만 조정해 도시지역에 걸치면
    절차가 통째로 바뀌므로, 어느 경로인지와 무엇이 그것을 갈랐는지를 남긴다.
    """
    urban, non_urban = _route_pair(ctx)
    if not urban or not non_urban:
        return                      # 이 발전원은 아직 경로 분기를 두지 않았다
    in_urban = bool(urban.get('applicable'))

    _note(doc,
          '※ **국토계획법 인허가 경로** — 「%s」와 「%s」는 함께 밟는 절차가 '
          '아니라 **택일**입니다. 도시계획시설로 결정하면 법 제56조제1항 단서에 '
          '따라 개발행위허가를 받지 않고 실시계획 인가(법 제88조)로 갈음합니다. '
          '풍력발전시설이 국토계획법상 기반시설인 「전기공급설비」(시행령 '
          '제2조제1항제3호)이기 때문에 생기는 분기이며, **가르는 것은 용도지역**'
          '입니다 — 설비용량이나 사업면적은 이 분기와 무관합니다.'
          % (non_urban.get('name') or '-', urban.get('name') or '-'))

    if in_urban:
        _note(doc,
              '※ 이 사업은 부지가 **도시지역(녹지지역 포함) 또는 지구단위계획'
              '구역에 걸쳐** 도시계획시설 결정이 의무입니다. 법 제43조제1항 '
              '본문이 기반시설을 도시·군관리계획으로 결정하도록 하고, 그 예외를 '
              '정한 시행령 제35조제1항제1호 가목의 목록에 전기공급설비가 없기 '
              '때문입니다. 개발행위허가로는 진행할 수 없습니다.')
    else:
        _note(doc,
              '※ 이 사업은 부지가 **도시지역 밖**(관리·농림·자연환경보전지역)이라 '
              '시행령 제35조제1항제2호 나목에 따라 도시·군관리계획 결정 없이 '
              '개발행위허가로 진행할 수 있습니다. 다만 이 조항은 결정 없이 설치'
              '**할 수 있다**는 면제이지 금지가 아니므로, **도시계획시설 경로를 '
              '택할 수도 있습니다.** 어느 쪽을 밟을지는 사업 여건에 따라 정해야 '
              '하므로 위 표에 두 경로를 모두 실었습니다.')

    _note(doc,
          '※ **도시계획시설 경로를 택하면 개별 인허가가 실시계획 인가·고시로 '
          '의제됩니다**(법 제92조제1항). 위 표의 「비고」에 의제 대상 절차를 '
          '호수와 함께 표시했습니다 — 산지전용·산지일시사용(13호), 농지전용·'
          '타용도 일시사용(8호), 도로점용(9호), 하천점용(26호), 초지전용(23호), '
          '사방지 지정해제(12호), 무연분묘 개장(10호). 개별 절차를 각각 받는 '
          '대신 실시계획 인가 하나로 갈음되므로 절차 수와 일정이 크게 달라집니다. '
          '여기에 **법 제95조 토지 등의 수용·사용권**까지 붙어, 사유지가 많은 '
          '부지에서는 이 경로가 유리할 수 있습니다.')

    _note(doc,
          '※ 다만 민간이 도시·군계획시설사업 **시행자로 지정**받으려면 법 '
          '제86조제5항·제7항과 시행령 제96조제2항에 따라 **대상 토지(국공유지 '
          '제외) 면적의 3분의 2 이상을 소유하고 토지소유자 총수의 2분의 1 이상의 '
          '동의**를 얻어야 합니다. 경로 선택 전에 토지 확보 수준을 먼저 '
          '확인하십시오.')


def _pending_steps(doc, ctx) -> None:
    """
    **대상 여부를 아직 가리지 못한 절차.**

    위 표는 해당하는 절차만 싣는다. 그런데 「해당하지 않음」과 「아직 못
    가림」을 같이 빼 버리면, 설비용량을 넣지 않았다는 이유로 환경영향평가가
    보고서에서 통째로 사라진다. 읽는 사람은 평가가 필요 없는 줄로 읽는다.

    그래서 사유가 `◇ 확인 필요`인 것만 따로 낸다 — 무엇을 먼저 정해야
    이 절차가 정해지는지가 곧 다음 할 일이다.
    """
    rows = [s for s in _all_steps(ctx)
            if not s.get('applicable')
            and '확인 필요' in (s.get('applicability_reason') or '')]
    if not rows:
        return
    doc.add_heading('대상 여부 미확정 절차 (%d건)' % len(rows), level=3)
    _styled_table(
        doc, ['절차', '소관', '무엇을 정해야 가려지는가'],
        [[s.get('name') or '-', s.get('authority') or '-',
          _summarize(s.get('applicability_reason') or '', 150)] for s in rows],
        accent='C07A1E', widths=[4.2, 3.6, 9.4])
    _note(doc, '※ 위 절차는 **해당하지 않는다고 판단한 것이 아니라**, 대상 여부를 '
               '가를 값(설비용량 등)이 아직 없어 판단을 미룬 것입니다. 값이 '
               '정해지면 자동으로 위 추진 순서에 편입됩니다.')


def _all_steps(ctx) -> list:
    """평가 결과에 들어 있는 인허가 로드맵 **전부** — 해당 여부와 무관하게."""
    for e in ctx.evals:
        steps = getattr(e.get('result'), 'permit_roadmap', None)
        if not steps:
            continue
        out = [s.to_dict() if hasattr(s, 'to_dict') else dict(s) for s in steps]
        return sorted(out, key=lambda d: d.get('order') or 0)
    return []


def _roadmap(ctx) -> list:
    """해당되는 절차만. 미확정 절차는 `_pending_steps`가 따로 낸다."""
    return [s for s in _all_steps(ctx) if s.get('applicable')]


# ======================================================================
# PART 4 — 부록
# ======================================================================
#: 데이터가 연동되지 않아 이 시스템이 판정하지 못하는 항목.
#:
#: 빠뜨리는 것과 '없다'고 말하는 것은 다르다. 육상풍력 인허가에서 실제로
#: 요구되는데 여기서 못 보는 항목을 밝혀 두지 않으면, 보고서를 받은 사람이
#: 검토가 끝난 줄로 읽는다.
NOT_REVIEWED = {
    'WIND': (
        ('환경', '섀도우 플리커 (Shadow Flicker)',
         '로터직경·허브고도·태양고도 기반 그림자 영향 — 입력값 미연동'),
        ('환경', '소음 · 저주파 예측',
         '풍력 소음예측 모델(야간 45dB(A) 기준) 미연동 — 실측·예측 별도 수행'),
        ('인프라', '대형 기자재 운송로 · 진입도로 폭원·회전반경',
         '블레이드 운송 가능성 검토 — 도로 제원 자료 미연동'),
    ),
    'SOLAR': (
        ('환경', '반사광 영향',
         '주변 주거·도로 대상 반사광 시뮬레이션 미연동'),
    ),
}


def appendix_table(doc, ctx) -> None:
    """
    법령·규제 전수 검토 결과표.

    70여 개 항목을 본문에 두면 결론이 묻힌다. 그렇다고 버릴 수는 없다 —
    '무엇을 봤는가'가 곧 이 검토의 범위이고, 협의 때 되짚을 근거다.
    그래서 한 표로 압축해 문서 끝에 둔다.
    """
    forest = 'FOREST' in _site_flags(ctx)
    doc.add_heading('법령 · 규제 전수 검토 결과 (%d개 항목)' % len(ctx.merged), level=2)
    _styled_table(
        doc, ['분야', '검토 항목', '판정', '법령 · 조례 근거', '소관 · 확인처'],
        [[m.get('category') or '기타', m['item_name'], _verdict(m, forest),
          _basis_line(m),
          m.get('data_source') or m.get('source_url') or '-']
         for m in ctx.merged],
        accent='55606C', status_col=2, widths=[1.9, 4.0, 2.0, 4.6, 4.9])
    if not forest and any(_forest_only(m['item_name']) for m in ctx.merged):
        _note(doc, FOREST_NOTE)
    _not_reviewed(doc, ctx)


#: 산지관리법이 적용되는 **산지**를 전제로만 뜻이 서는 항목.
#:
#: 산지가 없는 부지에서 이 항목들은 자료가 조회되지 않아 `◇ 확인 필요`로
#: 나온다. 그런데 그 표기는 "아직 안 봤다"로 읽혀, 산지가 아예 없는 부지에도
#: 산지 조사를 시켜 놓는다. 조회 실패와 대상 아님은 다른 말이다.
FOREST_ONLY = ('산사태위험등급', '산지구분', '지형 경사도', '평균경사도')

FOREST_NOTE = (
    '※ 「산사태위험등급 · 산지구분 · 지형 경사도」는 산지관리법이 적용되는 '
    '**산지**를 전제로 한 항목입니다. 이 부지에서는 임야 지목 · 산림보호구역 · '
    '산지구분 조회 어느 쪽에서도 산지가 확인되지 않아 「○ 해당없음(산지 아님)」'
    '으로 적었습니다. **자료 조회에 실패한 것이 아닙니다.** 부지 경계가 바뀌면 '
    '다시 확인하십시오.')


def _forest_only(name: str) -> bool:
    return any(k in name for k in FOREST_ONLY)


def _verdict(m: dict, forest: bool) -> str:
    """
    전수표 판정 칸 — 산지가 없는 부지의 산지 전용 항목은 표기를 바로잡는다.

    판정 자체(provider가 낸 `status`)는 건드리지 않는다. 자료가 없었다는
    사실은 그대로 두고, **그 사실이 이 부지에서 무엇을 뜻하는지**만 적는다.
    """
    if not forest and m['status'] == 'UNKNOWN' and _forest_only(m['item_name']):
        return _sig('POSSIBLE') + ' (산지 아님)'
    return _sig(m['status'])


def _site_flags(ctx) -> set:
    """판정 결과에서 인허가 분기 플래그를 다시 뽑는다. 로드맵과 같은 함수다."""
    from .engine import derive_site_flags
    res = next((e.get('result') for e in ctx.evals if e.get('result')), None)
    if res is None:
        return set()
    try:
        return derive_site_flags(res.analysis_items,
                                 getattr(ctx.prof, 'code', '') or 'SOLAR')
    except Exception:                                           # noqa: BLE001
        logger.exception('site_flags 재도출 실패')
        return set()


def _not_reviewed(doc, ctx) -> None:
    """미검토 항목 — 없는 것을 있는 척하지 않는다."""
    rows = NOT_REVIEWED.get(getattr(ctx.prof, 'code', ''), ())
    if not rows:
        return
    doc.add_heading('미검토 항목 (별도 검토 필요)', level=3)
    _styled_table(doc, ['분야', '항목', '사유'],
                  [[c, n, why] for c, n, why in rows],
                  accent='55606C', widths=[2.0, 5.0, 10.4])
    _note(doc, '※ 위 항목은 이 시스템이 자료를 연동하지 못해 **판정하지 않은** '
               '것입니다. 해당 없음이 아니므로 인허가 진행 시 별도로 검토하십시오.')
