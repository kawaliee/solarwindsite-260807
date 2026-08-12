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

from . import maps

logger = logging.getLogger(__name__)


def _fmt_ha(m2: float) -> str:
    return f'{m2 / 10_000:,.1f} ha'


def _pct(part: float, whole: float) -> str:
    return f'{(part / whole * 100):.1f}%' if whole else '-'


def build_area_report(result: dict, *, title_suffix: str = '') -> bytes:
    """available.compute*() 결과 → docx 바이트"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'
    style.font.size = Pt(10)

    total = result['total_area_m2']
    layout = result.get('layout')
    grand = result.get('grandfathering') or {}
    juris = result.get('jurisdictions') or []

    doc.add_heading('사업구역 제약도 검토 보고서', level=0)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.add_run(datetime.now().strftime('작성 %Y-%m-%d %H:%M')).font.size = Pt(9)

    # ── 1. 개요 ────────────────────────────────────────────────
    doc.add_heading('1. 검토 개요', level=1)
    rows = [('검토 대상',
             (f'발전기 {len(layout["turbines"])}기 배치선'
              if layout else '사업구역(폴리곤)') + (f' · {title_suffix}' if title_suffix else '')),
            ('검토 면적', _fmt_ha(total)),
            ('관할 지자체', ' · '.join(f'{j["sigungu"]} {j["ratio"] * 100:.1f}%'
                                   for j in juris) or '-')]
    if layout:
        rows += [('발전기 검토반경', f'{layout["turbine_radius_m"]:,} m'),
                 ('연결선 검토반경', f'{layout["corridor_radius_m"]:,} m')]
    _kv(doc, rows)

    # ── 2. 면적 분포 ───────────────────────────────────────────
    doc.add_heading('2. 면적 분포', level=1)
    _table(doc, ['구분', '면적', '비율', '설명'], [
        ['배제', _fmt_ha(result['blocked_m2']), _pct(result['blocked_m2'], total),
         '불가 판정 레이어 · 조례 이격거리 위반 범위'],
        ['조건부', _fmt_ha(result['conditional_m2']), _pct(result['conditional_m2'], total),
         '협의·저감 조건 하에 진행 가능'],
        ['제약 없음', _fmt_ha(result['free_m2']), _pct(result['free_m2'], total),
         '조회된 어떤 규제 레이어에도 걸리지 않음'],
        ['판정 보류', _fmt_ha(result['pending_m2']), _pct(result['pending_m2'], total),
         '조례를 확인하지 못한 지자체 구간'],
    ])

    doc.add_heading('가용면적', level=2)
    doc.add_paragraph(
        '가용면적은 하나로 말할 수 없어 두 가지로 병기합니다. 이 검토의 판정 기준은 '
        '생태자연도 1등급도 백두대간 핵심구역도 「불가」가 아니라 「조건부」로 봅니다. '
        '법률상 예외 행위가 있기 때문입니다. 어느 값을 쓸지는 사업 판단입니다.')
    _table(doc, ['구분', '면적', '비율'], [
        ['엄격 가용', _fmt_ha(result['available_strict_m2']),
         _pct(result['available_strict_m2'], total)],
        ['협의 포함 가용', _fmt_ha(result['available_with_consultation_m2']),
         _pct(result['available_with_consultation_m2'], total)],
    ])

    # ── 3. 제약도 ──────────────────────────────────────────────
    doc.add_heading('3. 제약도', level=1)
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
    doc.add_heading('4. 제약 사유별 면적', level=1)
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
    doc.add_heading('5. 지자체 조례', level=1)
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

    # ── 6. 한계 ────────────────────────────────────────────────
    doc.add_heading('6. 이 보고서의 한계', level=1)
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
    doc.add_paragraph()
    return t
