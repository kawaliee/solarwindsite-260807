"""
Fact-sheet → RAG 적재용 마크다운 생성기

`사업개요 빈 양식 v2` 의 서식 규칙을 코드로 고정한다.

  1. 섹션 제목마다 사업명을 반복한다
     → 검색은 섹션 단위로 쪼개져 들어온다. 사업명 없는 조각은
       "어느 사업 수치인지" 알 수 없어 교차 오염을 일으킨다.
  2. 별칭을 문서 머리에 명시한다
     → 문서명만으로 사업을 판정 못 하는 케이스(실측 31%) 대응.
  3. 빈칸 대신 `미확인` 을 출력한다
     → 빈칸은 LLM이 추측으로 메운다. 모른다고 명시해야 "자료에 없습니다"라고 답한다.
  4. 확정도를 함께 적는다
     → 예상치가 확정 사실처럼 답변되는 것을 막는다.
  5. 표 하나는 22행 이내로 끊는다
     → 청킹 시 표가 중간에서 잘리면 뒷조각은 헤더가 없어 의미 불명이 된다.

⚠️ 보안: 이 모듈은 값을 사용자가 입력한 그대로 옮길 뿐이며,
   계약가격·대주단 등 어떤 기밀 수치도 소스에 하드코딩하지 않는다.
"""
from __future__ import annotations

from .schema import SECTIONS, iter_fields, row_has_input

MAX_TABLE_ROWS = 22
UNKNOWN = '미확인'


# ── 값 포맷 ─────────────────────────────────────────────────────────

def _fmt_scalar(field, raw):
    """스칼라 값을 단위까지 붙여 문자열로 만든다. 비어 있으면 '' 반환."""
    if raw is None:
        return ''
    s = str(raw).strip()
    if not s:
        return ''
    unit = field.get('unit')
    if unit and field.get('type') == 'number':
        return f'{s} {unit}'
    return s


def _md_escape(s):
    """마크다운 표 셀 안에서 파이프가 열을 깨뜨리지 않게 한다."""
    return str(s).replace('|', '\\|').replace('\n', ' ')


def _cell(section_data, key):
    cell = section_data.get(key)
    return cell if isinstance(cell, dict) else {}


def _rows_of(section_data, key):
    v = _cell(section_data, key).get('v')
    return v if isinstance(v, list) else []


# ── 섹션 렌더링 ─────────────────────────────────────────────────────

def _render_scalar_rows(rows, out):
    """| 항목 | 값 | 확정도 | 비고 | 표를 22행 단위로 끊어 출력"""
    for i in range(0, len(rows), MAX_TABLE_ROWS):
        part = rows[i:i + MAX_TABLE_ROWS]
        if i:
            out.append(f'*(이어서 — {i // MAX_TABLE_ROWS + 1}부)*')
            out.append('')
        out.append('| 항목 | 값 | 확정도 | 비고 |')
        out.append('| --- | --- | --- | --- |')
        out.extend(part)
        out.append('')


#  '비대상'이 적힌 행의 나머지 빈 칸은 '미확인'이 아니라 '해당없음'이다.
#  둘을 뭉뚱그리면 "이 사업은 문화재 협의 대상이 아니다"라는 사실이 사라진다.
_NOT_APPLICABLE_MARKS = {'비대상', '해당없음', '미해당'}


def _render_table_field(field, section_data, out):
    cols = field.get('columns', [])
    rows = _rows_of(section_data, field['key'])
    label_key = cols[0]['key'] if cols else None
    # 양식이 미리 채워 둔 이름만 남은 행은 싣지 않는다.
    # 값이 전부 '미확인'인 표는 검색 노이즈만 만들고 답변에 기여하지 않는다.
    filled = [r for r in rows if row_has_input(r, field)]

    out.append(f'**{field["label"]}**')
    out.append('')
    if field.get('hint'):
        out.append(f'> {field["hint"]}')
        out.append('')

    if not filled:
        out.append(f'{UNKNOWN} — 이 항목은 아직 작성되지 않았습니다.')
        out.append('')
        return

    header = '| ' + ' | '.join(c['label'] for c in cols) + ' |'
    divider = '| ' + ' | '.join('---' for _ in cols) + ' |'

    body = []
    for r in filled:
        content_keys = [c['key'] for c in cols if c['key'] != label_key]
        not_applicable = any(
            str(r.get(k, '')).strip() in _NOT_APPLICABLE_MARKS for k in content_keys
        )
        blank_fill = '해당없음' if not_applicable else UNKNOWN

        cells = []
        for c in cols:
            raw = str(r.get(c['key'], '')).strip()
            if not raw:
                raw = blank_fill
            elif c.get('unit'):
                raw = f'{raw} {c["unit"]}'
            cells.append(_md_escape(raw))
        body.append('| ' + ' | '.join(cells) + ' |')

    for i in range(0, len(body), MAX_TABLE_ROWS):
        part = body[i:i + MAX_TABLE_ROWS]
        if i:
            out.append(f'*({field["label"]} — 이어서 {i // MAX_TABLE_ROWS + 1}부)*')
            out.append('')
        out.append(header)
        out.append(divider)
        out.extend(part)
        out.append('')


def _section_has_data(section, section_data):
    for _, f in iter_fields(section):
        if f['type'] == 'table':
            if any(row_has_input(r, f) for r in _rows_of(section_data, f['key'])):
                return True
        elif str(_cell(section_data, f['key']).get('v', '')).strip():
            return True
    return bool(str(section_data.get('_note', '')).strip())


def _render_section(section, section_data, project_name, out, lead_rows=None):
    out.append(f'## {section["no"]}. {section["title"]} — {project_name}')
    out.append('')

    if section.get('note'):
        out.append(f'> {section["note"]}')
        out.append('')

    # 그룹이 있으면 소제목으로 나눈다 (표가 25행을 넘지 않게 하는 장치이기도 하다)
    blocks = []
    if section.get('fields'):
        blocks.append((None, section['fields']))
    for g in section.get('groups', []):
        blocks.append((g.get('title'), g.get('fields', [])))

    for group_title, fields in blocks:
        if group_title:
            out.append(f'### {section["no"]}-{_group_index(section, group_title)}. '
                       f'{group_title} — {project_name}')
            out.append('')

        scalar_rows = list(lead_rows or []) if group_title is None else []
        lead_rows = None
        for f in fields:
            if f['type'] == 'table':
                if scalar_rows:
                    _render_scalar_rows(scalar_rows, out)
                    scalar_rows = []
                _render_table_field(f, section_data, out)
                continue

            cell = _cell(section_data, f['key'])
            value = _fmt_scalar(f, cell.get('v'))
            status = (cell.get('s') or '').strip()
            label = ('⭐ ' if f.get('star') else '') + f['label']
            if not value:
                # 값 자리에 이미 '미확인'을 적으므로 확정도까지 반복하지 않는다
                value, status = UNKNOWN, status or ''
            scalar_rows.append(
                f'| {_md_escape(label)} | {_md_escape(value)} | '
                f'{_md_escape(status or "-")} | {_md_escape(f.get("hint", ""))} |'
            )

        if scalar_rows:
            _render_scalar_rows(scalar_rows, out)

    note = str(section_data.get('_note', '')).strip()
    if note:
        out.append(f'> **근거 · 비고**: {_md_escape(note)}')
        out.append('')


def _group_index(section, group_title):
    for i, g in enumerate(section.get('groups', []), start=1):
        if g.get('title') == group_title:
            return i
    return 1


# ── 진입점 ──────────────────────────────────────────────────────────

def build_markdown(sheet) -> str:
    """ProjectFactSheet → 마크다운 문자열"""
    name = (sheet.name or '').strip() or '(사업명 미입력)'
    data = sheet.data or {}

    stage_label = dict(sheet.STAGE_CHOICES).get(sheet.stage, sheet.stage)
    aliases = [a for a in (sheet.aliases or []) if str(a).strip()]
    alias_line = ' / '.join([name, *aliases]) if aliases else name

    out = [
        f'# [사업개요] {name}',
        '',
        f'> **별칭**: {alias_line}',
        f'> **SPC**: {sheet.spc_name or UNKNOWN}',
        f'> **기준일**: {sheet.as_of_date or UNKNOWN} · '
        f'**작성**: {sheet.author or UNKNOWN} · **단계**: {stage_label}',
        '> **문서 성격**: 실무 담당자가 확인한 최신 사실 정리본. '
        '원본 문서와 수치가 다를 경우 본 문서를 우선하되, 차이가 있으면 그 사실을 함께 밝힐 것.',
        '',
        '**확정도 표기**: `확정` = 문서로 확인된 사실 · `예상` = 계획·추정치 · '
        '`미정` = 결정 전 · `미확인` = 조사 필요 · `해당없음`',
        '',
        '---',
        '',
    ]

    # 1번 섹션 표 머리에 식별 정보를 합류시킨다.
    # 사업명·SPC는 문서 머리에도 있지만, 청킹되면 1번 섹션 조각에는 남지 않는다.
    identity_rows = [
        f'| ⭐ 대표 사업명 | {_md_escape(name)} | 확정 |  |',
        f'| 별칭 | {_md_escape(alias_line)} | 확정 | 같은 사업을 부르는 다른 이름 |',
        f'| ⭐ SPC 법인명 | {_md_escape(sheet.spc_name or UNKNOWN)} | '
        f'{"확정" if sheet.spc_name else ""} |  |',
    ]

    empty_sections = []
    for section in SECTIONS:
        section_data = data.get(section['id'], {}) or {}
        is_basic = section['id'] == 'basic'

        if not is_basic and not _section_has_data(section, section_data):
            empty_sections.append(section)
            continue

        _render_section(section, section_data, name, out,
                        lead_rows=identity_rows if is_basic else None)
        out.append('---')
        out.append('')

    # 미작성 섹션은 한 덩어리로 모은다.
    # 섹션마다 "정보 없음" 문장을 흩뿌리면 서로 유사한 청크가 대량 생성되어
    # 정작 값이 들어 있는 청크를 검색 결과에서 밀어낸다.
    if empty_sections:
        out.append(f'## 미작성 항목 — {name}')
        out.append('')
        out.append(
            f'아래 항목은 {name} 사업개요에 **아직 정리되지 않았습니다.** '
            f'본 개요만으로는 답할 수 없으므로 원본 문서를 확인하거나 담당자에게 문의하십시오. '
            f'(작성되지 않았다는 뜻이며, 해당 사항이 없다는 뜻이 아닙니다.)'
        )
        out.append('')
        for s in empty_sections:
            out.append(f'- {s["no"]}. {s["title"]} — {s.get("subtitle", "")}')
        out.append('')
        out.append('---')
        out.append('')

    return '\n'.join(out).rstrip() + '\n'


def default_filename(sheet) -> str:
    """`[사업개요] {SPC명}.md` — SPC명이 없으면 사업명으로 대체"""
    base = (sheet.spc_name or sheet.name or 'untitled').strip()
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, '_')
    return f'[사업개요] {base}.md'
