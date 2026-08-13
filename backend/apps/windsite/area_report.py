"""
사업구역 제약도 보고서 (docx)
---------------------------------------------------------------
지점 검토 보고서(report.py)가 항목별 가부를 싣는다면, 이 보고서는 **면적이
어떻게 나뉘는가**를 싣는다. 수천 ha 구역은 어딘가 반드시 규제에 걸리므로
가부 판정이 성립하지 않기 때문이다.

■ 보고서는 유통되는 문서다

화면의 숫자는 다음 새로고침에 고쳐지고 각주가 숫자 옆에 붙어 있지만,
docx는 메일로 나가고 인쇄되고 보관된다. 표에서 숫자만 발췌돼 다른 문서에
인용되는 순간 단서는 떨어져 나간다. **각주보다 숫자가 오래 산다.**

그래서 이 보고서는 다음을 본문에 넣는다 — 부록이나 각주가 아니라.
  · 가용면적을 하나로 말하지 않고 엄격/협의 포함 두 가지로 병기
  · 조례 이격이 대장상 주택 기준의 **상한선**이라는 사실
  · 용도 미확인 건물 수, 조회 실패 레이어, 판정 보류 면적
  · 조례 경과규정 검토 대상이면 그 사실과 부칙 원문
"""
from __future__ import annotations

import io
import logging
from datetime import datetime

from . import available, kier, maps
from .providers import kepco

logger = logging.getLogger(__name__)


#: 판정 신호. 이모지(🔴🟡🟢)를 쓰지 않는다 — 한글 폰트에 없어 docx/PDF에서
#: 엉뚱한 글자로 대체된다(참조 보고서에서 '밃·밄·꾂'으로 깨져 있었다).
#: 아래 기호는 맑은 고딕에 모두 있다.
SIGNAL = {
    'IMPOSSIBLE': ('●', '불가', 0xC0, 0x39, 0x2B),
    'CONDITIONAL': ('▲', '조건부', 0xC0, 0x76, 0x00),
    'UNKNOWN': ('◇', '확인 필요', 0x66, 0x66, 0x66),
    'POSSIBLE': ('○', '해당없음', 0x1E, 0x7E, 0x34),
}


def _sig(status: str) -> str:
    m, label, *_ = SIGNAL.get(status, ('-', status, 0, 0, 0))
    return f'{m} {label}'


def _fmt_ha(m2: float) -> str:
    return f'{m2 / 10_000:,.1f} ha'


#: 1평 = 3.305785 m² (척관법 환산). 국내 부지 협의는 여전히 평으로 오간다.
PYEONG_M2 = 3.305785


def _fmt_area(m2: float) -> str:
    """면적은 ha와 평을 함께 적는다 — 지주 협의·매매는 평으로 오간다."""
    return f'{m2 / 10_000:,.1f} ha ({m2 / PYEONG_M2:,.0f}평)'


def _pct(part: float, whole: float) -> str:
    return f'{(part / whole * 100):.1f}%' if whole else '-'


#: 62개 항목을 묶어 보여줄 순서. 참조 보고서의 영역 구분과 맞춘다.
CATEGORY_ORDER = ['규제/법령', '안전/문화재', '환경', '산림',
                  '지자체 조례', '인프라', '사업성']

WIND_ITEM = '풍황(연평균 풍속)'
GRID_ITEM = '전력계통 연계(변전소·송전선로)'
QUIET_ITEM = '정온시설 이격거리(동심원 분석)'


def build_area_report(result: dict, evals: list | None = None, *,
                      title_suffix: str = '') -> bytes:
    """
    available.compute*() 결과 + 호기별 지점 검토 → docx 바이트

    evals가 있으면 규제 62개 항목·풍황·계통·정온시설을 호기별로 싣는다.
    면적 분포만으로는 '어느 호기가 무엇에 걸리는지'를 알 수 없어 배치를
    고칠 수 없기 때문이다.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'
    style.font.size = Pt(10)
    _init_label_index()
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(1.8)
        sec.top_margin = sec.bottom_margin = Cm(1.9)
    _page_numbers(doc, WD_ALIGN_PARAGRAPH)

    total = result['total_area_m2']
    layout = result.get('layout')
    grand = result.get('grandfathering') or {}
    juris = result.get('jurisdictions') or []

    jmeta = result.get('jurisdiction_meta') or {}
    outside = float(jmeta.get('uncovered_ratio') or 0)
    meta = [('검토 대상',
             (f'발전기 {len(layout["turbines"])}기 배치선'
              if layout else '사업구역(폴리곤)')),
            ('검토 면적', _fmt_area(total)),
            # 종전에는 '완도군 24.8%'만 적어 24.8%가 무엇의 비율인지 알 수 없었다.
            # 이 값은 검토 면적 중 그 지자체 관할이 차지하는 비율이며, 조례는
            # 관할 구역에만 적용되므로 배분을 밝혀야 판정을 읽을 수 있다.
            ('관할 지자체',
             (' · '.join(f'{j["sigungu"]} {j["ratio"] * 100:.1f}%' for j in juris)
              + (f' · 시군구 경계 밖 {outside * 100:.1f}%' if outside >= 0.005 else '')
              + '  (검토 면적 대비)') if juris else '-'),
            ('작성', datetime.now().strftime('%Y-%m-%d %H:%M'))]
    if layout:
        meta += [('발전기 검토반경', f'{layout["turbine_radius_m"]:,} m'),
                 ('연결선 검토반경', f'{layout["corridor_radius_m"]:,} m')]
    _cover(doc,
           '풍력 입지타당성 검토 보고서',
           (title_suffix or '사업구역·배치선 제약도 및 규제 종합평가'),
           meta)

    evals = [e for e in (evals or []) if e.get('result')]
    merged = available.merge_items(evals) if evals else []

    if evals:
        _part(doc, 1, '종합 판정', '한 장으로 보는 결과', BRAND)
        _overall(doc, evals, merged, result, Pt, RGBColor)

        _part(doc, 2, '입지조건 종합평가', f'검토 항목 {len(merged)}개 · 영역별', '2E6FB7')
        _assessment(doc, merged, Pt)
        _how_to_check(doc, merged)

        _part(doc, 3, '호기별 종합비교표', '어느 호기가 문제인가', '7C5BB5')
        _per_point(doc, evals)

        _part(doc, 4, '리스크 요약', '배치를 바꿔도 남는 것 / 옮기면 해소되는 것', 'B5426E')
        _common_risk(doc, merged, len(evals))
        base = 4
    else:
        base = 0

    _part(doc, base + 1, '면적 분포와 가용면적', '구역이 어떻게 나뉘는가', '18907E')
    _kpi(doc, [
        ('배제', _fmt_ha(result['blocked_m2']), _pct(result['blocked_m2'], total),
         STATUS_COLORS['IMPOSSIBLE']),
        ('조건부', _fmt_ha(result['conditional_m2']),
         _pct(result['conditional_m2'], total), STATUS_COLORS['CONDITIONAL']),
        ('제약 없음', _fmt_ha(result['free_m2']), _pct(result['free_m2'], total),
         STATUS_COLORS['POSSIBLE']),
        ('판정 보류', _fmt_ha(result['pending_m2']), _pct(result['pending_m2'], total),
         STATUS_COLORS['UNKNOWN']),
    ])
    if outside >= 0.05:
        # 해상·경계 밖은 어떤 시군구 조례도 적용되지 않아 '제약 없음'으로
        # 집계된다. 도서 지역에서는 이 몫이 절반을 넘기도 하므로, 가용면적을
        # 그대로 읽으면 실제보다 크게 본다.
        _callout(doc, '검토 면적의 %.1f%%가 시군구 경계 밖입니다' % (outside * 100),
                 '해상이거나 행정경계에 포함되지 않은 범위입니다. 이 부분에는 어떤 '
                 '지자체 조례도 적용되지 않아 아래 표에서 「제약 없음」으로 집계됩니다. '
                 '실제 부지로 쓸 수 없는 면적이 섞여 있으므로 가용면적을 그대로 '
                 '읽지 마십시오. 육상 면적만 필요하면 배치선을 육지 안으로 좁혀 '
                 '다시 검토하십시오.', tone='UNKNOWN')
    _styled_table(doc, ['구분', '면적', '비율', '설명'], [
        ['배제', _fmt_area(result['blocked_m2']), _pct(result['blocked_m2'], total),
         '불가 판정 레이어 · 조례 이격거리 위반 범위'],
        ['조건부', _fmt_area(result['conditional_m2']), _pct(result['conditional_m2'], total),
         '협의·저감 조건 하에 진행 가능'],
        ['제약 없음', _fmt_area(result['free_m2']), _pct(result['free_m2'], total),
         '조회된 어떤 규제 레이어에도 걸리지 않음'],
        ['판정 보류', _fmt_area(result['pending_m2']), _pct(result['pending_m2'], total),
         '조례를 확인하지 못한 지자체 구간'],
    ], accent='18907E', widths=[2.6, 4.6, 1.8, 7.0])

    doc.add_heading('가용면적', level=2)
    doc.add_paragraph(
        '가용면적은 하나로 말할 수 없어 두 가지로 병기합니다. 이 검토의 판정 기준은 '
        '생태자연도 1등급도 백두대간 핵심구역도 「불가」가 아니라 「조건부」로 봅니다. '
        '법률상 예외 행위가 있기 때문입니다. 어느 값을 쓸지는 사업 판단입니다.')
    _table(doc, ['구분', '면적', '비율'], [
        ['엄격 가용', _fmt_area(result['available_strict_m2']),
         _pct(result['available_strict_m2'], total)],
        ['협의 포함 가용', _fmt_area(result['available_with_consultation_m2']),
         _pct(result['available_with_consultation_m2'], total)],
    ])

    # ── 3. 제약도 ──────────────────────────────────────────────
    _part(doc, base + 2, '제약도', '위성영상 위 배제·조건부·제약없음', '18907E')
    g = result.get('geoms') or {}
    try:
        png = maps.constraint_map(
            g.get('area'), g.get('blocked'), g.get('conditional'), g.get('free'),
            turbines=_turbine_points(layout))
        doc.add_picture(io.BytesIO(png), width=Cm(16))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception as e:                                      # noqa: BLE001
        logger.warning('제약도 생성 실패: %s', e)
        doc.add_paragraph('※ 제약도를 생성하지 못했습니다. 배경지도 조회 실패일 수 있습니다.')

    # ── 4. 제약 사유 ───────────────────────────────────────────
    _part(doc, base + 3, '제약 사유별 면적', '무엇이 얼마나 차지하는가', '3C8C3C')
    reasons = result.get('by_reason') or []
    if reasons:
        _table(doc, ['제약 사유', '판정', '면적', '비율'],
               [[r['layer'] + (' (잠정)' if r.get('provisional') else ''),
                 r['status'], _fmt_ha(r['area_m2']), _pct(r['area_m2'], total)]
                for r in reasons])
    else:
        doc.add_paragraph('해당 없음')

    blanket = result.get('blanket') or []
    if blanket:
        doc.add_heading('구역 전체 조건', level=2)
        doc.add_paragraph(
            '아래 항목은 구역을 통째로 덮어 구역 안의 위치를 가르지 못합니다. '
            '면적 분할에서는 제외했으나 협의 대상인 것은 사실입니다.')
        for b in blanket:
            doc.add_paragraph(f'· {b["layer"]}', style='List Bullet')

    zoning = result.get('zoning') or []
    if zoning:
        doc.add_heading('용도지역 구성', level=2)
        doc.add_paragraph(
            '용도지역은 국토계획법이 전 국토를 도시·관리·농림·자연환경보전 4종으로 '
            '빈틈없이 나눈 분류입니다. 보호구역 같은 「지정」과 성격이 달라 제약 '
            '면적과는 별도 축으로 집계했습니다.')
        _table(doc, ['용도지역', '면적', '비율'],
               [[z['layer'], _fmt_ha(z['area_m2']), _pct(z['area_m2'], total)]
                for z in zoning])

    # ── 5. 조례 ────────────────────────────────────────────────
    _part(doc, base + 4, '지자체 조례', '이격거리와 경과규정', 'C07A1E')
    _table(doc, ['지자체', '구역 내 비율', '조례 상태', '최대 이격거리'],
           [[j['sigungu'], f'{j["ratio"] * 100:.1f}%',
             _ord_state(j.get('ordinance_state', '')),
             f'{j["max_distance_m"]:,} m' if j.get('max_distance_m') else '-']
            for j in juris])

    if grand.get('ordinances'):
        doc.add_heading('경과규정 검토', level=2)
        for o in grand['ordinances']:
            basis = ('경과조치를 담은 개정의 시행일'
                     if o.get('cutoff_is_transition')
                     else '경과조치 부칙을 찾지 못해 조례 최신 시행일로 대신함')
            doc.add_paragraph(
                f'· {o["sigungu"]} {o["ordinance"]} {o["article"]} — '
                f'기준일 {o["cutoff_date"]} ({basis})', style='List Bullet')
        note = doc.add_paragraph(grand.get('note') or '')
        note.runs[0].font.color.rgb = RGBColor(0xC0, 0x60, 0x00) if grand.get(
            'review_required') else RGBColor(0x55, 0x55, 0x55)

        if grand.get('review_required') and grand.get('free_if_exempt_m2') is not None:
            _table(doc, ['시나리오', '제약 없음 면적', '비율'], [
                ['현행 조례 적용', _fmt_ha(result['free_m2']),
                 _pct(result['free_m2'], total)],
                ['조례 이격 미적용 (참고)', _fmt_ha(grand['free_if_exempt_m2']),
                 _pct(grand['free_if_exempt_m2'], total)],
            ])
            doc.add_paragraph(
                '※ 「조례 이격 미적용」은 참고용 시나리오이며 면제 확정이 아닙니다. '
                '부칙 경과조치가 해당 사업에 적용되는지는 관할 지자체가 판단합니다.')

        addenda = next((o['addenda'] for o in grand['ordinances'] if o.get('addenda')), '')
        if addenda:
            doc.add_heading('부칙 원문 (경과조치·적용례)', level=3)
            r = doc.add_paragraph(addenda[:3000]).runs[0]
            r.font.size = Pt(8.5)

    # ── 풍황·계통·정온시설 ─────────────────────────────────────
    if evals:
        _part(doc, base + 5, '풍황 · 전력계통 · 정온시설', '사업성과 인프라', 'C05E2E')
        _detail_sections(doc, evals, Pt)

    # ── 한계 ───────────────────────────────────────────────────
    _part(doc, base + 6, '이 보고서의 한계', '수치만 발췌해 인용하지 마십시오', '55606C')
    doc.add_paragraph(
        '아래 사항은 결과 수치에 직접 영향을 줍니다. 수치만 발췌해 인용하지 마십시오.')
    limits = list(result.get('notes') or [])
    fails = result.get('fetch_failures') or []
    if fails:
        limits.append(
            f'조회하지 못한 레이어가 {len(fails)}건 있습니다. 보지 못한 제약이 있어 '
            f'가용면적이 실제보다 크게 나올 수 있습니다: ' + ' / '.join(fails[:5]))
    jm = result.get('jurisdiction_meta') or {}
    if jm.get('unverified'):
        limits.append('조례를 확인하지 못한 지자체: ' + ', '.join(jm['unverified']))
    if jm.get('uncovered_m2'):
        limits.append(
            f'어느 시군구 경계에도 포함되지 않은 면적 {_fmt_ha(jm["uncovered_m2"])} '
            f'(대개 해상). 해당 구간은 조례 판정에서 빠져 있습니다.')
    limits.append(
        '풍황은 실측 대상이므로 이 검토에 포함되지 않습니다. 사업성 판단에는 '
        '별도의 풍황 계측이 필요합니다.')
    for t in limits:
        doc.add_paragraph(f'· {t}', style='List Bullet')

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _turbine_points(layout):
    if not layout:
        return None
    from . import geo
    return [geo.point_metric(a, o) for a, o in layout['turbines']]


_ORD_STATE = {
    'HAS_RULES': '이격 조례 적용',
    'NO_RULE': '이격 조례 없음 (원문 확인)',
    'NOT_FOUND': '조례 미확인',
    'UNVERIFIED': '조회 실패',
}


def _ord_state(key: str) -> str:
    return _ORD_STATE.get(key, key or '-')


def _kv(doc, pairs):
    t = doc.add_table(rows=0, cols=2)
    t.style = 'Light Grid Accent 1'
    for k, v in pairs:
        row = t.add_row().cells
        row[0].text, row[1].text = str(k), str(v)
    doc.add_paragraph()
    return t


def _table(doc, headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Light Grid Accent 1'
    for i, h in enumerate(headers):
        t.rows[0].cells[i].text = h
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = str(v)
    _table_flow(t)
    doc.add_paragraph()
    return t


# ======================================================================
# 호기별 검토를 쓰는 섹션들
# ======================================================================
def _worst(evals: list) -> str:
    order = ['POSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'IMPOSSIBLE']
    grades = [e['result'].overall_feasibility.grade for e in evals]
    return max(grades, key=order.index) if grades else 'UNKNOWN'


def _item_of(res, name: str):
    return next((i for i in res.analysis_items if i.item_name == name), None)


def _overall(doc, evals, merged, area, Pt, RGBColor) -> None:
    """
    한 장짜리 종합 판정 — 신호등과 점수를 함께 낸다.

    점수만 실으면 표에서 숫자만 발췌돼 절대 기준처럼 인용된다. 신호등만
    실으면 후보지 간 우열을 가릴 수 없다. 둘을 같이 두고, 점수가 상대
    지표라는 사실을 바로 옆에 적는다.
    """
    scores = [e['result'].overall_feasibility.score for e in evals]
    counts = {k: sum(1 for m in merged if m['status'] == k)
              for k in ('IMPOSSIBLE', 'CONDITIONAL', 'UNKNOWN', 'POSSIBLE')}

    wind = next((_item_of(e['result'], WIND_ITEM) for e in evals), None)
    grid = next((_item_of(e['result'], GRID_ITEM) for e in evals), None)

    rows = [
        ('종합 판정',
         '%s · 점수 %d~%d점 (호기 %d기)'
         % (_sig(_worst(evals)), min(scores), max(scores), len(evals))),
        ('검토 항목',
         '%d개 — %s %d건 · %s %d건 · %s %d건 · %s %d건'
         % (len(merged), _sig('IMPOSSIBLE'), counts['IMPOSSIBLE'],
            _sig('CONDITIONAL'), counts['CONDITIONAL'],
            _sig('UNKNOWN'), counts['UNKNOWN'],
            _sig('POSSIBLE'), counts['POSSIBLE'])),
        ('가용면적',
         '엄격 %s · 협의 포함 %s'
         % (_fmt_ha(area['available_strict_m2']),
            _fmt_ha(area['available_with_consultation_m2']))),
    ]
    if wind:
        rows.append(('풍력 자원', '%s — %s' % (_sig(wind.status.value), wind.reason)))
    if grid:
        rows.append(('계통 연계', '%s — %s' % (_sig(grid.status.value), grid.reason)))
    _kv(doc, rows)

    p = doc.add_paragraph(
        '※ 점수는 후보지 간 상대 비교용 지표이며 법적 판단이나 절대 기준이 '
        '아닙니다. 항목별 판정과 함께 읽으십시오. 여러 호기 중 가장 나쁜 값을 '
        '종합 판정으로 씁니다 — 한 기라도 걸리면 그 항목은 해결해야 하기 때문입니다.')
    p.runs[0].font.size = Pt(8.5)
    p.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    _score_rule(doc, evals)


def _score_rule(doc, evals) -> None:
    """
    점수가 어떻게 나왔는지 그 자리에서 밝힌다.

    산식을 감추면 '46점'이 무슨 뜻인지 물어볼 데가 없다. 특히 이 산식은
    감점을 단순 합산하지 않는다 — 레이어를 늘릴수록 점수가 나빠지면
    데이터가 좋아진 것을 나쁜 결과로 표시하는 셈이기 때문이다.
    """
    from . import engine

    doc.add_heading('점수 산식', level=2)
    doc.add_paragraph(
        '100점에서 세 가지를 뺍니다. 불가 항목이 하나라도 있으면 산식과 무관하게 '
        '0점입니다.')
    _table(doc, ['감점 항목', '계산', '뜻'], [
        ['worst', '가장 불리한 한 항목의 (판정 감점 + 난이도 감점)', '리스크의 크기'],
        ['spread', '조건부 항목 수에서 1을 뺀 값 × 3점', '리스크의 개수'],
        ['gap', '확인 필요 항목 수 × 2점', '정보 부족'],
    ])

    def _key(k):
        return k.value if hasattr(k, 'value') else str(k)

    diff_label = {'LOW': '낮음', 'MEDIUM': '보통', 'HIGH': '높음', 'CRITICAL': '치명'}
    pen = ' · '.join(f'{SIGNAL.get(_key(k), ("", _key(k)))[1]} {v}점'
                     for k, v in engine.STATUS_PENALTY.items())
    dif = ' · '.join(f'{diff_label.get(_key(k), _key(k))} {v}점'
                     for k, v in engine.DIFFICULTY_PENALTY.items())
    _note(doc, f'판정 감점 — {pen}')
    _note(doc, f'난이도 감점 — {dif}')

    # 실제 이 검토의 숫자로 한 번 풀어 보인다. 산식만 적으면 대조가 안 된다.
    worst_e = min(evals, key=lambda e: e['result'].overall_feasibility.score)
    items = worst_e['result'].analysis_items
    cond = [i for i in items if i.status.value == 'CONDITIONAL']
    unk = [i for i in items if i.status.value == 'UNKNOWN']
    w = max((engine.STATUS_PENALTY[i.status] + engine.DIFFICULTY_PENALTY[i.difficulty]
             for i in cond + unk), default=0)
    sp, gp = 3 * max(0, len(cond) - 1), 2 * len(unk)
    _note(doc,
          '이 검토의 최저점(%d호기) — 100 − worst %d − spread %d(조건부 %d건) '
          '− gap %d(확인 필요 %d건) = %d점'
          % (worst_e['no'], w, sp, len(cond), gp, len(unk),
             max(0, min(100, 100 - (w + sp + gp)))),
          color=INK)


def _how_to_check(doc, merged) -> None:
    """
    조건부·확인 필요 항목을 **무엇을 근거로 어디서** 확인하는지.

    판정만 적고 끝내면 읽는 사람이 항목마다 소관 기관을 다시 찾아야 한다.
    근거 규정과 확인처는 판정을 낼 때 이미 알고 있는 값이므로 함께 싣는다.
    """
    rows = []
    for m in merged:
        if m['status'] not in ('IMPOSSIBLE', 'CONDITIONAL', 'UNKNOWN'):
            continue
        basis = ' '.join(x for x in (m.get('law'), m.get('article')) if x) or '-'
        how = m.get('action_required') or '-'
        where = m.get('source_url') or ''
        src = m.get('data_source') or ''
        rows.append([m['item_name'], _sig(m['status']), basis,
                     how + (f'\n▸ {where}' if where else '')
                         + (f'\n(판정 자료: {src})' if src else '')])
    if not rows:
        return
    doc.add_heading('조건부·확인 필요 항목 — 무엇을 어디서 확인하나', level=2)
    doc.add_paragraph(
        '아래는 판정의 근거 규정과, 그 판정을 확정하기 위해 실제로 밟아야 하는 '
        '절차입니다. 링크는 해당 자료를 제공하는 기관의 조회처입니다.')
    _styled_table(doc, ['항목', '판정', '근거 규정', '확인 방법 · 조회처'], rows,
                  accent='2E6FB7', status_col=1,
                  widths=[3.8, 2.0, 3.6, 7.6])


def _assessment(doc, merged, Pt) -> None:
    """62개 항목을 영역별로 묶어 신호등으로 낸다 (참조 보고서의 '입지조건 종합평가')."""
    if not merged:
        doc.add_paragraph('호기별 검토 결과가 없습니다.')
        return
    by_cat = {}
    for m in merged:
        by_cat.setdefault(m['category'] or '기타', []).append(m)

    for cat in CATEGORY_ORDER + [c for c in by_cat if c not in CATEGORY_ORDER]:
        items = by_cat.get(cat)
        if not items:
            continue
        worst = max(items, key=lambda m: available._SEVERITY[m['status']])['status']
        doc.add_heading('%s (%d개) — %s' % (cat, len(items), _sig(worst)), level=2)
        _styled_table(doc, ['항목', '판정', '해당 호기', '주요 결과'],
               [[m['item_name'], _sig(m['status']),
                 (m.get('hit_label') or '-' if m['status'] != 'POSSIBLE' else '-'),
                 (m['reason'] or '')]
                for m in items],
                      accent=SECTION_COLORS.get(cat, DEFAULT_SECTION_COLOR),
                      status_col=1, widths=[4.2, 2.2, 3.4, 7.2])


def _per_point(doc, evals) -> None:
    """호기별 비교표 — 어느 기가 문제인지 한눈에 보이게 한다."""
    rows = []
    for e in evals:
        r = e['result']
        o = r.overall_feasibility
        bad = sum(1 for i in r.analysis_items if i.status.value == 'IMPOSSIBLE')
        cond = sum(1 for i in r.analysis_items if i.status.value == 'CONDITIONAL')
        rows.append([
            '%d호기' % e['no'],
            (e['address'] or '%.5f, %.5f' % (e['lat'], e['lng']))[:34],
            '%s %d점' % (_sig(o.grade), o.score),
            '%d건' % bad, '%d건' % cond,
            _short(_item_of(r, WIND_ITEM)), _short(_item_of(r, GRID_ITEM)),
        ])
    _styled_table(doc, ['호기', '위치', '판정·점수', '불가', '조건부', '풍황', '계통'],
                  rows, accent='7C5BB5', widths=[1.5, 5.4, 2.8, 1.3, 1.5, 2.4, 1.9])


def _short(item) -> str:
    """
    비교표 칸에 들어갈 한 줄 요약.

    풍황은 **허브고도 100m 환산값**으로 적는다. 관측소 원측정치(대개 지상
    10m)를 그대로 실으면 '2.5 m/s'처럼 사업이 성립하지 않는 숫자로 보이는데,
    그것은 관측 높이가 다르기 때문이지 이 부지의 바람이 아니다.

    계통은 **154kV 이상**을 먼저 적는다. 최근접이라도 배전급이면 풍력
    연계점이 될 수 없어, 그 거리는 사업 판단에 쓸 수 없는 숫자다.
    """
    if item is None:
        return '-'
    raw = item.raw or {}
    hub = raw.get('hub_extrapolation') or {}
    obs = raw.get('observation') or {}
    if isinstance(obs.get('mean_ws'), (int, float)):
        lo, hi = (hub.get('judge_range_ms') or [None, None])[:2]
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            return '%.1f~%.1f m/s@%dm' % (lo, hi, hub.get('judge_hub_m') or 100)
        h = (raw.get('station') or {}).get('anemometer_h_m')
        return '%.1f m/s%s' % (obs['mean_ws'], ('@%.0fm' % h) if h else '')
    subs = [s for s in (raw.get('substations') or []) if s.get('distance_m')]
    if subs:
        hv = [s for s in subs if (s.get('voltage') or 0) >= 154_000]
        pick = min(hv or subs, key=lambda s: s['distance_m'])
        kv = (pick.get('voltage') or 0) // 1000
        return '%.2f km%s' % (pick['distance_m'] / 1000,
                              (' (%dkV)' % kv) if kv else ' (전압 미상)')
    return SIGNAL.get(item.status.value, ('-',))[0]


def _common_risk(doc, merged, n_points: int) -> None:
    """모든(또는 다수) 호기에 공통으로 걸린 항목 — 배치를 바꿔도 남는 제약이다."""
    common = [m for m in merged
              if m['status'] in ('IMPOSSIBLE', 'CONDITIONAL', 'UNKNOWN')
              and m['hits'] >= max(1, n_points)]
    partial = [m for m in merged
               if m['status'] in ('IMPOSSIBLE', 'CONDITIONAL')
               and 0 < m['hits'] < n_points]

    doc.add_paragraph('전체 %d기 공통 제약 — 배치를 바꿔도 남습니다.' % n_points)
    if common:
        for m in common:
            # 여기는 이미 '공통' 항목만 모은 자리라 '(전 호기)'가 겹친다.
            # total을 넘기지 않아 번호만 받는다.
            doc.add_paragraph(
                '· %s — %s (%s)'
                % (m['item_name'], _sig(m['status']),
                   available.nos_label(m.get('hit_nos') or [])),
                style='List Bullet')
    else:
        doc.add_paragraph('· 공통으로 걸리는 항목 없음', style='List Bullet')

    if partial:
        doc.add_paragraph('일부 호기만 해당 — 해당 호기를 옮기면 해소될 수 있습니다.')
        for m in partial:
            # 여기 적히는 호기는 모두 같은 판정을 받은 것이다. 그중 하나를
            # '최악'으로 지목하면 나머지가 더 나은 것처럼 읽히므로 적지 않는다.
            doc.add_paragraph(
                '· %s — %s · %s'
                % (m['item_name'], _sig(m['status']), m.get('hit_label') or '-'),
                style='List Bullet')


def _detail_sections(doc, evals, Pt) -> None:
    """풍황·전력계통·정온시설 — 참조 보고서의 5·6·7장에 대응."""
    doc.add_heading('풍력자원', level=2)
    rows = []
    for e in evals:
        it = _item_of(e['result'], WIND_ITEM)
        if not it:
            continue
        raw = it.raw or {}
        st, obs = raw.get('station') or {}, raw.get('observation') or {}
        hub = raw.get('hub_extrapolation') or {}
        lo, hi = (hub.get('judge_range_ms') or [None, None])[:2]
        rows.append([
            '%d호기' % e['no'],
            '%s(%s) %.1fkm' % (st.get('name', '-'), st.get('stn_id', '-'),
                               st.get('distance_km', 0)),
            '%.2f m/s' % obs['mean_ws'] if isinstance(obs.get('mean_ws'), (int, float)) else '-',
            '%.0f m' % st['anemometer_h_m'] if st.get('anemometer_h_m') else '-',
            # 판정도 사업 판단도 허브고도 기준이다. 이 열을 표의 중심으로 둔다.
            ('%.1f~%.1f m/s' % (lo, hi)
             if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else '-'),
            obs.get('period', '-'),
        ])
    if rows:
        hub_m = 0
        for e in evals:
            it = _item_of(e['result'], WIND_ITEM)
            hub_m = ((it.raw or {}).get('hub_extrapolation') or {}).get('judge_hub_m') if it else 0
            if hub_m:
                break
        _styled_table(doc,
                      ['호기', '관측소', '관측 연평균', '관측높이',
                       '허브고도 %dm 환산' % (hub_m or 100), '관측기간'],
                      rows, accent='C05E2E',
                      widths=[1.5, 4.0, 2.2, 1.8, 3.2, 4.1])
        first = _item_of(evals[0]['result'], WIND_ITEM)
        if first:
            doc.add_paragraph(first.reason)
        _note(doc,
              '환산은 멱법칙(power law)에 전단지수 α를 개활지~산림 범위로 적용한 '
              '구간 추정입니다. 관측 연평균은 관측소 원측정치(대개 지상 10m)라 '
              '허브고도 값과 그대로 비교할 수 없습니다.')

    _kier(doc, evals)
    _callout(doc, '풍황 수치는 참고자료입니다 — 실제 관측 데이터를 사용하십시오',
             '위 값은 기상관측소·재분석 자료를 허브고도로 환산한 추정치이며, '
             '지형에 따른 국지 가속·감속을 담지 못합니다. 발전량 산정과 투자 '
             '판단에는 현장 풍황탑(또는 라이다) 실측 최소 1년 자료를 쓰십시오. '
             '이 보고서의 풍황 판정은 후보지 선별용입니다.',
             tone='UNKNOWN')

    doc.add_heading('전력계통 인프라', level=2)
    grid = next((it for e in evals
                 for it in [_item_of(e['result'], GRID_ITEM)] if it), None)
    if grid:
        doc.add_paragraph(grid.reason)
        subs = [s for s in ((grid.raw or {}).get('substations') or [])
                if (s.get('name') or '') != '(명칭 미상)'
                and (s.get('voltage') or 0) >= 154_000]
        subs.sort(key=lambda s: s.get('distance_m') or 0)
        if subs:
            _table(doc,
                   ['변전소', '전압(kV)', '직선거리', '변전소 여유(kW)', '선로 여유(kW)'],
                   [[s.get('name', ''), '%d' % ((s.get('voltage') or 0) // 1000),
                     '%.2f km' % ((s.get('distance_m') or 0) / 1000),
                     ('{:,}'.format(s['bank_margin_kw'])
                      if s.get('bank_margin_kw') is not None else '-'),
                     ('{:,}'.format(s['line_margin_kw'])
                      if s.get('line_margin_kw') is not None else '-')]
                    for s in subs[:3]])
        doc.add_paragraph(
            '※ 여유용량은 조회 시점 스냅샷이며 선점으로 변동합니다. '
            '실제 연계는 한전 협의로 확정됩니다.')
    else:
        _note(doc, '※ OSM 변전소 정보를 조회하지 못했습니다(사용량 제한 등). '
                   '거리 판정은 빠져 있으나 아래 공급관계는 확인됩니다.')

    _supply(doc, evals)

    doc.add_heading('정온시설 이격거리', level=2)
    rows = [['%d호기' % e['no'], _sig(it.status.value), (it.reason or '')]
            for e in evals
            for it in [_item_of(e['result'], QUIET_ITEM)] if it]
    if rows:
        _table(doc, ['호기', '판정', '내용'], rows)
    else:
        doc.add_paragraph('정온시설 판정 결과가 없습니다.')


# ======================================================================
# 보고서 디자인
# ----------------------------------------------------------------------
# docx는 CSS가 없어 색을 셀 음영(w:shd)과 글자색으로만 낼 수 있다. 그래서
# '표를 레이아웃 도구로 쓴다' — 표지 색 밴드, PART 구분면, KPI 카드가 모두
# 테두리 없는 표다. 아래 헬퍼가 그 반복을 감춘다.
#
# 색은 의미와 1:1로 묶는다. 예쁘라고 칠하지 않는다 — 같은 색이 어디서나
# 같은 뜻이어야 표를 훑어 읽을 수 있다.
# ======================================================================
INK = '1B2733'          # 본문
MUTED = '6B7A8C'        # 보조 설명
LINE = 'DDE3EA'         # 옅은 구분선
BRAND = '1B4F8C'        # 표지·머리글
BRAND_LIGHT = 'EAF1F9'

#: 영역별 색 — 참조 자료의 PART 구분처럼 단원마다 색을 달리해 위치를 알린다
SECTION_COLORS = {
    '규제/법령': '2E6FB7',
    '안전/문화재': '7C5BB5',
    '환경': '18907E',
    '산림': '3C8C3C',
    '지자체 조례': 'C07A1E',
    '인프라': 'C05E2E',
    '사업성': 'B5426E',
}
DEFAULT_SECTION_COLOR = BRAND

#: 판정별 (글자색, 배경색). 표 어디서나 같은 뜻으로 쓴다.
STATUS_COLORS = {
    'IMPOSSIBLE': ('B0202A', 'FBE4E5'),
    'CONDITIONAL': ('9A5B00', 'FDF0DC'),
    'UNKNOWN': ('55606C', 'EFF2F5'),
    'POSSIBLE': ('1B6B31', 'E6F4EA'),
}


def _shade(cell, hex_color: str) -> None:
    """셀 배경색. python-docx가 노출하지 않아 XML을 직접 넣는다."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    el = OxmlElement('w:shd')
    el.set(qn('w:val'), 'clear')
    el.set(qn('w:fill'), hex_color)
    cell._tc.get_or_add_tcPr().append(el)


def _no_borders(table) -> None:
    """테두리 제거 — 표를 레이아웃 도구로 쓸 때."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        e = OxmlElement(f'w:{edge}')
        e.set(qn('w:val'), 'none')
        e.set(qn('w:sz'), '0')
        borders.append(e)
    table._tbl.tblPr.append(borders)


def _run(par, text, *, size=10, bold=False, color=INK, name='맑은 고딕'):
    from docx.shared import Pt, RGBColor
    r = par.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    r.font.name = name
    r.font.color.rgb = RGBColor.from_string(color)
    return r


def _cover(doc, title: str, subtitle: str, meta: list) -> None:
    """표지 — 색 밴드 위에 제목, 아래에 메타 정보."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    for _ in range(3):
        doc.add_paragraph()

    band = doc.add_table(rows=1, cols=1)
    _no_borders(band)
    c = band.rows[0].cells[0]
    _shade(c, BRAND)
    p = c.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _run(p, '\n' + title + '\n', size=26, bold=True, color='FFFFFF')
    p2 = c.add_paragraph()
    _run(p2, subtitle + '\n', size=12, color='CFE0F2')

    doc.add_paragraph()
    t = doc.add_table(rows=0, cols=2)
    _no_borders(t)
    for k, v in meta:
        row = t.add_row().cells
        _run(row[0].paragraphs[0], k, size=9.5, color=MUTED)
        _run(row[1].paragraphs[0], str(v), size=10.5, bold=True)
    doc.add_page_break()


def _part(doc, no, title: str, subtitle: str = '', color: str = BRAND) -> None:
    """PART 구분면 — 단원 앞에 색 밴드를 두어 어디를 읽고 있는지 알린다."""
    t = doc.add_table(rows=1, cols=1)
    _no_borders(t)
    c = t.rows[0].cells[0]
    _shade(c, color)
    p = c.paragraphs[0]
    _run(p, f'  PART {no}   ', size=10, bold=True, color='FFFFFF')
    _run(p, title, size=15, bold=True, color='FFFFFF')
    if subtitle:
        p2 = c.add_paragraph()
        _run(p2, '  ' + subtitle, size=9, color='E3EDF7')
    doc.add_paragraph()


def _kpi(doc, cards: list) -> None:
    """
    KPI 카드 줄 — [(라벨, 값, 보조설명, 색)].

    가장 중요한 숫자를 표 안에 묻지 않고 크게 띄운다. 보고서를 넘겨보는
    사람은 표를 읽지 않고 큰 숫자만 본다.
    """
    if not cards:
        return
    t = doc.add_table(rows=1, cols=len(cards))
    _no_borders(t)
    for cell, (label, value, note, color) in zip(t.rows[0].cells, cards):
        _shade(cell, color[1] if isinstance(color, tuple) else BRAND_LIGHT)
        p = cell.paragraphs[0]
        _run(p, label, size=8.5, color=MUTED)
        p2 = cell.add_paragraph()
        _run(p2, str(value), size=16, bold=True,
             color=color[0] if isinstance(color, tuple) else BRAND)
        if note:
            p3 = cell.add_paragraph()
            _run(p3, note, size=8, color=MUTED)
    doc.add_paragraph()


def _styled_table(doc, headers: list, rows: list, *, accent: str = BRAND,
                  status_col: int | None = None, widths: list | None = None):
    """
    머리글에 색을 넣고 줄무늬를 준 표.

    status_col을 주면 그 열을 판정 색으로 칠한다. 표를 훑을 때 색만 보고
    문제 행을 찾을 수 있어야 한다 — 글자를 다 읽게 만들면 안 본다.
    """
    from docx.shared import Cm

    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Table Grid'
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        _shade(cell, accent)
        cell.text = ''
        _run(cell.paragraphs[0], h, size=9, bold=True, color='FFFFFF')
    for n, r in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ''
            text = '' if v is None else str(v)
            color = INK
            if status_col is not None and i == status_col:
                key = next((k for k in STATUS_COLORS if k in _STATUS_BY_LABEL.get(text, '')),
                           None)
                if key:
                    fg, bg = STATUS_COLORS[key]
                    _shade(cells[i], bg)
                    color = fg
            elif n % 2 == 1:
                _shade(cells[i], 'F7F9FB')
            _run(cells[i].paragraphs[0], text, size=9, color=color,
                 bold=(status_col is not None and i == status_col))
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                if w:
                    row.cells[i].width = Cm(w)
    _table_flow(t)
    doc.add_paragraph()
    return t


def _table_flow(t) -> None:
    """
    쪽이 넘어갈 때 표가 읽히도록 두 가지를 건다.

      · 머리글 행을 다음 쪽에도 다시 찍는다 — 두 번째 쪽부터 어느 열이
        무엇인지 알 수 없으면 표가 아니라 글자 더미가 된다
      · 한 행이 쪽 경계에서 쪼개지지 않게 한다 — 첨부 사진처럼 '3호기'만
        앞 쪽에 남고 내용은 다음 쪽으로 넘어가면 짝을 못 맞춘다
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    hdr = OxmlElement('w:tblHeader')
    t.rows[0]._tr.get_or_add_trPr().append(hdr)
    for row in t.rows:
        cant = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(cant)


def _page_numbers(doc, WD_ALIGN_PARAGRAPH) -> None:
    """
    바닥글에 'N / M' 쪽번호.

    docx의 쪽 수는 파일을 만드는 시점에 알 수 없다(글꼴·여백에 따라 Word가
    다시 흘린다). 그래서 숫자를 직접 쓰지 않고 필드 코드를 심어 Word가
    열 때 계산하게 한다.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def field(par, instr: str):
        run = par.add_run()
        begin = OxmlElement('w:fldChar'); begin.set(qn('w:fldCharType'), 'begin')
        instr_el = OxmlElement('w:instrText')
        instr_el.set(qn('xml:space'), 'preserve'); instr_el.text = f' {instr} '
        end = OxmlElement('w:fldChar'); end.set(qn('w:fldCharType'), 'end')
        run._r.append(begin); run._r.append(instr_el); run._r.append(end)
        run.font.size = __import__('docx.shared', fromlist=['Pt']).Pt(8.5)
        run.font.name = '맑은 고딕'
        return run

    for sec in doc.sections:
        p = sec.footer.paragraphs[0] if sec.footer.paragraphs else sec.footer.add_paragraph()
        p.text = ''
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        field(p, 'PAGE')
        _run(p, ' / ', size=8.5, color=MUTED)
        field(p, 'NUMPAGES')


#: 표에 찍힌 '● 불가' 같은 문자열을 다시 상태 키로 되돌리기 위한 역인덱스
_STATUS_BY_LABEL = {}


def _init_label_index():
    for k, (mark, label, *_rest) in SIGNAL.items():
        _STATUS_BY_LABEL[f'{mark} {label}'] = k


def _note(doc, text: str, *, color: str = MUTED, size: float = 8.5):
    p = doc.add_paragraph()
    _run(p, text, size=size, color=color)
    return p


def _callout(doc, title: str, body: str, *, tone: str = 'CONDITIONAL') -> None:
    """
    강조 박스 — 놓치면 안 되는 단서를 본문 흐름에서 띄운다.

    각주로 내리면 숫자만 발췌돼 인용될 때 떨어져 나간다.
    """
    fg, bg = STATUS_COLORS.get(tone, STATUS_COLORS['UNKNOWN'])
    t = doc.add_table(rows=1, cols=1)
    _no_borders(t)
    c = t.rows[0].cells[0]
    _shade(c, bg)
    _run(c.paragraphs[0], title, size=9.5, bold=True, color=fg)
    p = c.add_paragraph()
    _run(p, body, size=8.5, color=INK)
    doc.add_paragraph()


def _kier(doc, evals) -> None:
    """
    KIER 격자 풍황 — 고도별·방위별 참고 수치.

    **판정에 쓰지 않는다.** 시각을 지정하는 파라미터가 없고 값의 크기가
    연평균으로 보기에 너무 낮아 순간 풍속으로 판단된다. 그럼에도 싣는 이유는
    ASOS 관측소가 수십 km 떨어져 있는 반면 이 격자는 부지 위에 있어,
    고도에 따른 증가폭과 방위별 편차를 보는 데 쓸모가 있기 때문이다.
    """
    rep = evals[0] if evals else None
    if not rep:
        return
    rows = kier.fetch(rep['lat'], rep['lng'])
    s = kier.summarize(rows, rep['lat'], rep['lng'])
    if not s:
        _note(doc, '※ KIER 격자 풍황을 조회하지 못했습니다.')
        return

    doc.add_heading('KIER 격자 풍황 (참고)', level=3)
    alt = s['by_altitude_ms']
    _styled_table(doc, ['고도'] + [f'{k}m' for k in alt],
                  [['풍속(m/s)'] + [f'{v:.2f}' for v in alt.values()]],
                  accent='8E6BB5')

    azi = s['by_azimuth_ms']
    top = sorted(azi.items(), key=lambda x: -x[1])[:6]
    _styled_table(doc, ['방위각'] + [f'{k}°' for k, _ in top],
                  [['풍속(m/s)'] + [f'{v:.2f}' for _, v in top]],
                  accent='8E6BB5')

    _callout(
        doc, '이 값은 판정에 쓰지 않았습니다',
        f"{s['caveat']} 격자 {s['grid_points']}점(부지 반경 "
        f"{s['radius_km'] or '-'}km) · 표본 {s['samples']:,}건. "
        f"방위별로는 {s['dominant_azimuth_deg']}° 섹터가 가장 높으나, "
        f"순간값 기반이라 연간 주풍향으로 단정할 수 없습니다. "
        f"사업성 판정은 기상청 ASOS 연평균 기준으로 별도 산출했습니다.",
        tone='UNKNOWN')


def _supply(doc, evals) -> None:
    """
    공급가능 변전소 — 한전 공식 자료.

    OSM은 '가까운 변전소'를 알려주지만 가깝다고 그 변전소에서 공급받는 것은
    아니다. 이 자료는 읍면동마다 실제로 공급하는 변전소를 알려주므로 성격이
    다르고, Overpass가 막혀도 조회된다.

    다만 변전소명이 첫 글자만 남고 가려져 있다(국가기밀시설). 여유용량 자료와
    첫 글자로 대조해 후보를 좁히되, 하나로 좁혀지지 않으면 좁히지 않는다 —
    임의로 고르면 엉뚱한 변전소의 여유용량을 붙이게 된다.
    """
    rep = next((e for e in evals if e.get('sigungu')), None)
    if not rep:
        return
    emd = ''
    for part in (rep.get('address') or '').split():
        if part.endswith(('읍', '면', '동')):
            emd = part
            break
    info = kepco.supply_for(rep.get('sido', ''), rep['sigungu'], emd)
    if not info:
        return

    # 후보를 전국에서 고르면 '삼*'에 삼계·삼미·삼죽·삼척이 모두 걸린다.
    # 도 단위로 줄여야 대개 하나로 특정된다.
    try:
        rows = kepco.KepcoGridClient.fetch(
            metro_cd=kepco.METRO_CD.get(rep.get('sido', ''), ''))
    except Exception:                                           # noqa: BLE001
        rows = []
    known = sorted({r.get('substNm', '') for r in rows if r.get('substNm')})
    margins = kepco.KepcoGridClient.summarize(rows) if rows else {}

    table = []
    for masked in info['names']:
        cand = kepco.match_masked(masked, known)
        if len(cand) == 1:
            rec = margins.get(kepco.normalize_substation(cand[0])) or {}
            table.append([
                masked, cand[0],
                f"{rec.get('substation_margin_kw', 0):,.0f}" if rec else '-',
                f"{rec.get('best_line_margin_kw', 0):,.0f}" if rec else '-',
            ])
        else:
            table.append([masked,
                          ' / '.join(cand) if cand else '대조 실패',
                          '-', '-'])

    doc.add_heading('공급가능 변전소 (한전 공식)', level=3)
    _styled_table(doc,
                  ['공급변전소(비식별)', '대조 결과', '변전소 여유(kW)', '선로 여유(kW)'],
                  table, accent='C05E2E', widths=[3.6, 4.4, 3.4, 3.4])
    _callout(
        doc, f'{info["scope"]} 기준 공급가능 변전소 {len(info["names"])}개소',
        '한전 공식 자료라 거리 기반 추정과 다릅니다 — 가깝다고 그 변전소에서 '
        '공급받는 것은 아니고, 멀어도 공급 대상일 수 있습니다. 변전소명은 '
        '국가기밀시설이라 첫 글자만 공개되며, 여유용량 자료와 첫 글자로 '
        '대조했습니다. 후보가 둘 이상이면 좁히지 않고 그대로 적었습니다.',
        tone='UNKNOWN')
