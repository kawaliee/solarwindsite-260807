"""
보고서 표현 계층 (docx) — 팔레트 · 서식 · 레이아웃 primitive
---------------------------------------------------------------
docx는 CSS가 없어 색을 셀 음영(w:shd)과 글자색으로만 낼 수 있다. 그래서
'표를 레이아웃 도구로 쓴다' — 표지 색 밴드, PART 구분면, KPI 카드가 모두
테두리 없는 표다. 아래 헬퍼가 그 반복을 감춘다.

색은 의미와 1:1로 묶는다. 예쁘라고 칠하지 않는다 — 같은 색이 어디서나
같은 뜻이어야 표를 훑어 읽을 수 있다.

■ 왜 따로 뺐나

보고서 본문(area_report.py)이 '무엇을 싣는가'라면 이 모듈은 '어떻게
보이는가'다. 둘이 한 파일에 있으면 문단 하나를 고치려도 1,700줄을 뒤져야
하고, 새 보고서 구성을 짤 때마다 서식 코드가 복사된다.
"""
from __future__ import annotations

import logging
import re

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


#: 1평 = 3.305785 m² (척관법 환산). 국내 부지 협의는 여전히 평으로 오간다.
PYEONG_M2 = 3.305785


def _fmt_ha(m2: float) -> str:
    """
    좁은 칸에 들어갈 짧은 면적 — **평이 먼저**다.

    지주 협의·매매·보상이 전부 평으로 오간다. ha는 도면과 인허가 서류에서
    쓰이지만, 보고서를 받아 실제로 땅을 사고 협의하는 사람에게는 평이 곧
    감각이다. 그래서 평을 앞에 두고 ha를 괄호로 덧붙인다.
    """
    return f'{m2 / PYEONG_M2:,.0f}평 ({m2 / 10_000:,.1f} ha)'


def _fmt_area(m2: float) -> str:
    """면적 전체 표기 — 평·ha·㎡ 순. 넓은 칸에 쓴다."""
    return f'{m2 / PYEONG_M2:,.0f}평 ({m2 / 10_000:,.1f} ha · {m2:,.0f}㎡)'


def _pct(part: float, whole: float) -> str:
    return f'{(part / whole * 100):.1f}%' if whole else '-'


def site_signature(geom) -> tuple:
    """
    사업구역 도형의 지문 — (면적 ㎡, 정점 수, WKB 해시 12자리).

    보고서의 모든 지도는 같은 사업구역 도형을 그려야 한다. 지도 하나가
    다른 도형을 그리면(단순화·첫 링만 취하기·별도 재생성) 문서 안에서
    구역 경계가 지도마다 달라지고, 그 도형으로 판정까지 하면 결과가
    틀린다. `Ctx.site()`가 접근 때마다 이 지문을 기준과 대조한다.
    """
    import hashlib

    def count_pts(g):
        parts = getattr(g, 'geoms', [g])
        n = 0
        for p in parts:
            ext = getattr(p, 'exterior', None)
            if ext is not None:
                n += len(ext.coords)
                n += sum(len(r.coords) for r in p.interiors)
            else:
                n += len(getattr(p, 'coords', []))
        return n

    return (round(float(geom.area), 1), count_pts(geom),
            hashlib.sha1(geom.wkb).hexdigest()[:12])


def _para(doc, text, *, style: str | None = None):
    """
    본문 문단 하나. `**강조**`를 굵게로 바꾼다.

    `doc.add_paragraph(text)`를 그대로 쓰면 provider가 쓴 강조 표기가 별표인
    채로 찍힌다. 판정 사유·안내는 이 함수로 넣는다.
    """
    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _md_text_into(p, text)
    return p


def _md_text_into(p, text) -> None:
    """문단에 글을 넣으며 `**강조**`만 굵게로 바꾼다. 서식은 건드리지 않는다."""
    text = str(text or '')
    if '**' not in text:
        p.add_run(text)
        return
    last = 0
    for m in _BOLD_MD.finditer(text):
        if m.start() > last:
            p.add_run(text[last:m.start()])
        p.add_run(m.group(1)).bold = True
        last = m.end()
    if last < len(text):
        p.add_run(text[last:])


def _md_text(cell, text) -> None:
    """스타일이 붙은 표 칸에 글을 넣는다 — 서식은 표 스타일에 맡긴다."""
    p = cell.paragraphs[0]
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    _md_text_into(p, text)


def _kv(doc, pairs):
    t = doc.add_table(rows=0, cols=2)
    t.style = 'Light Grid Accent 1'
    for k, v in pairs:
        row = t.add_row().cells
        _md_text(row[0], k)
        _md_text(row[1], v)
    fix_table(t, doc, [1.0, 3.2])
    doc.add_paragraph()
    return t


def _table(doc, headers, rows, ratios: list | None = None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Light Grid Accent 1'
    for i, h in enumerate(headers):
        _md_text(t.rows[0].cells[i], h)
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            _md_text(cells[i], v)
    fix_table(t, doc, ratios)
    _table_flow(t)
    doc.add_paragraph()
    return t


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


#: `**강조**` 표기. 판정 사유·안내 문구가 이 표기로 쓰여 있다.
_BOLD_MD = re.compile(r'\*\*(.+?)\*\*', re.S)


def _run(par, text, *, size=10, bold=False, color=INK, name='맑은 고딕'):
    """
    글 한 토막을 문단에 넣는다. `**...**`는 **실제 굵게**로 바꾼다.

    provider와 안내 문구가 강조를 마크다운으로 쓰는데, docx에는 그런 문법이
    없어 종전에는 별표가 그대로 찍혔다(한 문서에 23곳). 「**군도**」처럼
    별표가 남으면 강조가 되기는커녕 오탈자로 읽힌다.
    """
    from docx.shared import Pt, RGBColor

    def emit(chunk: str, strong: bool):
        r = par.add_run(chunk)
        r.font.size = Pt(size)
        r.bold = bold or strong
        r.font.name = name
        r.font.color.rgb = RGBColor.from_string(color)
        return r

    text = text or ''
    if '**' not in text:
        return emit(text, False)

    last, r = 0, None
    for m in _BOLD_MD.finditer(text):
        if m.start() > last:
            r = emit(text[last:m.start()], False)
        r = emit(m.group(1), True)
        last = m.end()
    if last < len(text):
        r = emit(text[last:], False)
    # 짝이 맞지 않아 아무것도 못 바꿨으면 남은 별표를 지운다 — 화면에
    # 별표를 남기느니 강조를 잃는 편이 낫다.
    return r if r is not None else emit(text.replace('**', ''), False)


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
    fix_table(band, doc)

    doc.add_paragraph()
    t = doc.add_table(rows=0, cols=2)
    _no_borders(t)
    for k, v in meta:
        row = t.add_row().cells
        _run(row[0].paragraphs[0], k, size=9.5, color=MUTED)
        _run(row[1].paragraphs[0], str(v), size=10.5, bold=True)
    fix_table(t, doc, [1.0, 3.2])
    doc.add_page_break()


def _part(doc, no, title: str, subtitle: str = '', color: str = BRAND) -> None:
    """
    PART 구분면 — 단원 앞에 색 밴드를 두어 어디를 읽고 있는지 알린다.

    PART 2부터는 새 페이지에서 시작한다. 종전에는 앞 PART의 마지막 표·
    카드 바로 아래에 이어 붙어, 단원이 바뀌었는데도 한 페이지 중간에서
    시작하는 것처럼 보였다(실측 지적: PART 2·3 경계). PART 1은 표지
    (`_cover`)가 이미 페이지를 넘겨 두므로 여기서 또 넘기면 빈 페이지가
    생긴다 — 그래서 PART 1만 예외로 둔다.
    """
    if no != 1:
        doc.add_page_break()
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
    fix_table(t, doc)
    doc.add_paragraph()


def _sub_band(doc, label: str, headline: str = '', color: str = BRAND) -> None:
    """
    소제목 띠 — PART 헤더보다 한 단 낮은 위계.

    한 카드 안에 항목이 여럿 이어지면(③ 환경성의 농업진흥지역도·생태자연도·
    철새도래지) 같은 모양의 2열 표가 연달아 나와, 어디서 한 항목이 끝나고
    다음이 시작되는지 읽히지 않는다. 항목마다 옅은 띠를 얹어 경계를 만든다.
    """
    t = doc.add_table(rows=1, cols=1)
    _no_borders(t)
    c = t.rows[0].cells[0]
    _shade(c, _tint(color))
    p = c.paragraphs[0]
    _run(p, label, size=9.5, bold=True, color=color)
    if headline:
        _run(p, '   ' + headline, size=8.5, color=MUTED)
    fix_table(t, doc)


#: 소제목 띠 배경 — 같은 색을 흰색과 섞어 옅게 만든다. 색을 따로 정해 두면
#: 카드 색을 바꿀 때마다 짝을 맞춰야 한다.
_TINT = 0.88


def _tint(hex_color: str) -> str:
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 'F1F3F6'
    return '%02X%02X%02X' % tuple(
        int(v + (255 - v) * _TINT) for v in (r, g, b))


def _hairline(doc, color: str = LINE) -> None:
    """항목 사이를 가르는 실선 한 줄."""
    t = doc.add_table(rows=1, cols=1)
    _no_borders(t)
    c = t.rows[0].cells[0]
    _shade(c, color)
    c.paragraphs[0].paragraph_format.space_before = 0
    c.paragraphs[0].paragraph_format.space_after = 0
    _run(c.paragraphs[0], '', size=1)
    fix_table(t, doc)


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
    fix_table(t, doc)
    doc.add_paragraph()


# ======================================================================
# 가로폭 — 문서에서 읽어 하나로 쓴다
# ----------------------------------------------------------------------
# 표·지도·제목 밴드의 좌우 끝선이 한 선에 맞아야 문서가 정돈돼 보인다.
# 그러려면 '본문 폭'이 딱 하나여야 하는데, 종전에는 cm 값을 손으로 적어
# 두었다(BODY_W_CM = 17.4). 그 값은 A4(21.0cm) 기준이었고 실제 문서는
# python-docx 기본 서식인 **Letter(21.59cm)** 라, 표를 폭에 맞춰도 매번
# 0.6cm씩 어긋났다.
#
# 그래서 값을 적지 않고 **섹션에서 계산**한다. 페이지 크기나 여백을 바꾸면
# 표도 지도도 저절로 따라온다.
# ======================================================================
#: 1 twip = 635 EMU (1인치 = 1440 twips = 914,400 EMU)
EMU_PER_TWIP = 635
#: 표 셀의 기본 좌우 여백(twips). python-docx 기본 표 스타일 값이다.
CELL_MARGIN_TWIPS = 108


def content_width(doc) -> tuple[int, int]:
    """
    본문 유효 폭 → (twips, EMU).

    페이지 폭에서 좌우 여백을 뺀 값이다. 이 문서에서는
    12,240 − 1,020×2 = 10,200 twips = 6,477,000 EMU = 17.99cm.
    """
    s = doc.sections[0]
    emu = int(s.page_width - s.left_margin - s.right_margin)
    return emu // EMU_PER_TWIP, emu


def split_widths(total_twips: int, ratios: list | None, cols: int) -> list:
    """
    열 폭을 twips로 나눈다. **합계가 정확히 total_twips가 된다.**

    반올림 오차를 마지막 열이 흡수한다 — 열 폭 합이 표 폭과 1 twip이라도
    어긋나면 Word가 표를 제멋대로 늘려 끝선이 틀어진다.
    """
    ratios = [r for r in (ratios or [])] or [1.0] * cols
    if len(ratios) != cols:                      # 열 수가 안 맞으면 균등 분할
        ratios = [1.0] * cols
    s = sum(r for r in ratios if r) or 1.0
    out = [int(total_twips * (r or 0) / s) for r in ratios]
    out[-1] += total_twips - sum(out)            # 나머지는 마지막 열이 받는다
    return out


def _styled_table(doc, headers: list, rows: list, *, accent: str = BRAND,
                  status_col: int | None = None, widths: list | None = None,
                  center: bool = False):
    """
    머리글에 색을 넣고 줄무늬를 준 표. widths는 열 폭 **비율**이다.

    status_col을 주면 그 열을 판정 색으로 칠한다. 표를 훑을 때 색만 보고
    문제 행을 찾을 수 있어야 한다 — 글자를 다 읽게 만들면 안 본다.
    """
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
    fix_table(t, doc, widths, center=center)
    _table_flow(t)
    doc.add_paragraph()
    return t


def fix_table(t, doc, ratios: list | None = None, *, center: bool = False):
    """
    표를 **본문 폭에 정확히** 맞춘다. 모든 표가 이 함수를 거친다.

    python-docx가 만드는 표는 기본이 `tblW type="auto" w="0"`이라 내용에
    따라 폭이 제각각이 된다(실측: 한 문서 안 34개 표가 전부 제각각).
    다음 셋을 함께 걸어야 Word가 폭을 지킨다.

      · tblW      type=dxa, w=본문 폭        — 표 전체 폭을 못 박는다
      · tblLayout type=fixed                — 내용에 따라 늘리지 못하게 한다
      · gridCol   합계 = 표 폭               — 열 폭이 어긋나면 다시 벌어진다
      · tblInd    = 좌측 셀 여백             — 표 테두리를 여백선에 맞춘다

    ⚠️ **tblInd가 없으면 폭이 맞아도 끝선이 어긋난다.**

    `tblInd`를 적지 않으면 Word는 첫 칸의 *글자*가 여백선에서 시작하도록
    표를 놓는다. 그래서 표 **테두리**는 셀 좌측 여백(108twips = 5.4pt)만큼
    왼쪽으로 밀려 나간다. PDF로 렌더링해 재 보니 그림은 x=51.0~561.0pt인데
    표 테두리는 45.6~555.7pt였다 — 폭은 똑같이 510pt인데 통째로 1.9mm
    왼쪽에 놓여 있었다. 눈에 보이는 어긋남이 이것이다.

    tblInd를 셀 여백만큼 주면 테두리가 여백선에 붙어 그림과 끝이 맞는다.

    ratios는 열 폭 **비율**이다(cm이 아니다). 주지 않으면 균등 분할한다.
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu

    twips, _ = content_width(doc)
    cols = len(t.columns)
    widths = split_widths(twips, ratios, cols)

    tblPr = t._tbl.tblPr
    for tag in ('w:tblW', 'w:tblInd', 'w:tblLayout'):
        for el in tblPr.findall(qn(tag)):
            tblPr.remove(el)
    w = OxmlElement('w:tblW')
    w.set(qn('w:type'), 'dxa')
    w.set(qn('w:w'), str(twips))
    tblPr.append(w)
    ind = OxmlElement('w:tblInd')
    ind.set(qn('w:type'), 'dxa')
    ind.set(qn('w:w'), str(CELL_MARGIN_TWIPS))
    tblPr.append(ind)
    layout = OxmlElement('w:tblLayout')
    layout.set(qn('w:type'), 'fixed')
    tblPr.append(layout)

    t.autofit = False
    if center:
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 열 폭은 tblGrid와 **모든 행의 셀**에 같이 적어야 Word가 따른다.
    for row in t.rows:
        for i, cw in enumerate(widths):
            if i < len(row.cells):
                row.cells[i].width = Emu(cw * EMU_PER_TWIP)
    return widths


def image_width_emu(doc, cols: int = 1) -> int:
    """
    그림 폭(EMU). 1열이면 본문 전폭, N열 격자면 셀 폭에서 여백을 뺀 값.

    임의의 cm 값을 쓰지 않는다 — 표와 그림이 같은 계산에서 나와야 끝선이
    맞는다.
    """
    twips, emu = content_width(doc)
    if cols <= 1:
        return emu
    return cell_image_emu(twips // cols)


def cell_image_emu(col_twips: int) -> int:
    """
    폭이 `col_twips`인 셀 안에 들어가는 그림의 EMU 폭.

    격자처럼 열 폭이 같지 않은 카드(지도 48% · 설명 52%)에서도 같은
    계산을 쓰기 위해 열 폭을 직접 받는다. `fix_table()`이 돌려주는
    열 폭 목록을 그대로 넣으면 된다.
    """
    return max(1, col_twips - CELL_MARGIN_TWIPS * 2) * EMU_PER_TWIP


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
    fix_table(t, doc)
    doc.add_paragraph()
