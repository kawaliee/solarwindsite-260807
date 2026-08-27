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

from . import (available, energy as energy_mod, kier, maps, report_cards,
               report_style)
from .providers import kepco
from .report_style import (  # noqa: F401  (본문이 그대로 쓰는 서식 primitive)
    BRAND, BRAND_LIGHT, DEFAULT_SECTION_COLOR, INK, LINE, MUTED, PYEONG_M2,
    SECTION_COLORS, SIGNAL, STATUS_COLORS,
    _callout, _cover, _fmt_area, _fmt_ha, _init_label_index, _kpi, _kv,
    _no_borders, _note, _page_numbers, _part, _pct, _run, _shade, _sig,
    _styled_table, _table, _table_flow,
)

logger = logging.getLogger(__name__)


WIND_ITEM = '풍황(연평균 풍속)'
GRID_ITEM = '전력계통 연계(변전소·송전선로)'
QUIET_ITEM = '정온시설 이격거리(동심원 분석)'


def build_area_report(result: dict, evals: list | None = None, *,
                      title_suffix: str = '',
                      energy: str | None = None,
                      map_image: bytes | None = None,
                      env_images: dict[str, bytes] | None = None,
                      project_name: str = '') -> bytes:
    """
    available.compute*() 결과 + 호기별 지점 검토 → docx 바이트

    evals가 있으면 규제 62개 항목·풍황·계통·정온시설을 호기별로 싣는다.
    면적 분포만으로는 '어느 호기가 무엇에 걸리는지'를 알 수 없어 배치를
    고칠 수 없기 때문이다.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Emu, Pt, RGBColor

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'
    style.font.size = Pt(10)
    _init_label_index()
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(1.8)
        sec.top_margin = sec.bottom_margin = Cm(1.9)
    _page_numbers(doc, WD_ALIGN_PARAGRAPH)

    # 에너지원은 계산 결과에 실려 온다. 인자로 덮어쓸 수 있게 두되,
    # 둘 다 없으면 풍력으로 본다(종전 동작).
    prof = energy_mod.profile(energy or result.get('energy_type'))

    total = result['total_area_m2']
    layout = result.get('layout')
    grand = result.get('grandfathering') or {}
    juris = result.get('jurisdictions') or []

    jmeta = result.get('jurisdiction_meta') or {}
    outside = float(jmeta.get('uncovered_ratio') or 0)
    parcel = result.get('parcel')
    # 사업명은 **맨 앞에** 둔다. 같은 검토 시스템에서 나온 문서가 여러 사업지
    # 것으로 쌓이는데, 표지에 사업명이 없으면 열어서 좌표를 봐야 어느 사업인지
    # 안다. 파일명(`_report_filename`)과 같은 값을 쓴다.
    meta = ([('사업명', project_name.strip())] if project_name.strip() else []) + [
            ('검토 대상',
             (f'발전기 {len(layout["turbines"])}기 배치선' if layout
              else f'필지 {parcel["count"]}필 (PNU 기준)' if parcel
              else '사업구역(폴리곤)')),
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
    how = ('사업구역·배치선 제약도 및 규제 종합평가' if layout
           else '필지 단위 제약도 및 규제 종합평가' if parcel
           else '사업구역 제약도 및 규제 종합평가')
    _cover(doc,
           prof.report_title,
           title_suffix or ((f'{project_name.strip()}  ·  {how}')
                            if project_name.strip() else how),
           meta)

    evals = [e for e in (evals or []) if e.get('result')]
    merged = available.merge_items(evals) if evals else []

    ctx = report_cards.Ctx(result=result, evals=evals, merged=merged, prof=prof,
                           map_image=map_image, env_images=env_images or {})

    if evals:
        _part(doc, 1, '종합 판정', '한 장으로 보는 결과', BRAND)
        _summary_map(doc, result, layout, Cm, WD_ALIGN_PARAGRAPH, map_image)
        report_cards.summary_dashboard(doc, ctx)
        # ⚠️ `_setback_table`을 여기서 부르지 않는다. 같은 「개발행위허가
        #    이격거리 기준」 표가 PART 2 ① 조례 이격거리 분석에 이미 있어
        #    한 문서에 두 번 실렸다.

        _part(doc, 2, '필수 검토 항목별 공간 분석',
              '지도로 보는 핵심 제약', '2E6FB7')
        report_cards.gis_map_cards(doc, ctx)

        # 리스크 매트릭스는 뺐다. 규제/법령·산사태·국가유산·조례·환경·산림·
        # 계통은 PART 1 핵심쟁점과 PART 2 지도 카드에서 이미 다룬 항목이라,
        # 같은 내용을 판정만 바꿔 한 번 더 나열하는 표였다.
        _part(doc, 3, '인허가 추진 순서',
              '무엇을 · 어느 순서로 밟을 것인가', 'B5426E')
        report_cards.milestone_checklist(doc, ctx)
        if layout:
            # 호기별 비교는 배치선 검토에서만 뜻이 선다 — 어느 호기를
            # 옮길지가 곧 리스크 해소 방법이다.
            doc.add_heading('호기별 판정 비교', level=2)
            _per_point(doc, evals)

    # ── PART 4 부록 ────────────────────────────────────────────
    # 아래는 전부 '근거'다. 결론을 뒷받침하지만 결론보다 먼저 읽힐 이유가
    # 없어 문서 끝으로 내린다. 버리지는 않는다 — 협의 때 되짚을 자료다.
    _part(doc, 4, '부록 — 법령·규제 전수 검토', '근거 자료 · 상세 데이터', '55606C')
    if evals:
        report_cards.appendix_table(doc, ctx)

    if parcel:
        doc.add_heading('검토 필지 내역', level=2)
        _parcels(doc, parcel, Pt)

    if _precedent_item(merged):
        doc.add_heading('인허가 사례 대조 (참고)', level=2)
        _precedents(doc, evals, merged)

    # ── 삭제한 부록 절 ──────────────────────────────────────────
    # 「면적 분포와 가용면적」·「제약 사유별 면적」·「지자체 조례 · 경과규정」·
    # 「전력계통 · 정온시설 상세」를 걷어냈다. 앞의 PART가 같은 내용을 이미
    # 다루거나(계통 표는 PART 2 ⑤ 카드에 그대로 있다), 결론과 무관한 원자료라
    # 부록이 본문보다 길어져 있었다.
    #
    # 다만 「면적 분포와 가용면적」에만 있던 것 셋은 버리면 안 된다 —
    # 엄격/협의 포함 가용면적과 시군구 경계 밖 경고다. 특히 협의 포함
    # 가용면적은 PART 1의 **예상 설비용량을 산출한 바로 그 값**이라,
    # 지우면 용량의 근거가 문서에서 사라진다. PART 1 4분할 아래로 옮겼다
    # (report_cards._usable_area).
    doc.add_heading('이 보고서의 한계', level=2)
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
        report_style._para(doc, f'· {t}', style='List Bullet')

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


#: 부지로 쓸 수 없는 공공용지 성격 지목 (providers/cadastral.py의 EXCLUDED와 동일)
_PUBLIC_JIMOK = ('도로', '하천', '구거', '제방', '철도용지', '수도용지')


#: 사례 대조 항목의 이름. 판정 목록에서 이 항목을 찾아 절을 낸다.
PRECEDENT_ITEM = '인허가 사례 대조 (참고)'


def _precedent_item(merged):
    return next((m for m in (merged or [])
                 if m['item_name'] == PRECEDENT_ITEM), None)


def _precedent_raw(evals) -> dict:
    """
    사례 항목이 남긴 지역·에너지원.

    판정을 합치는 단계(`available.merge_items`)에서 `raw`가 떨어져 나가므로
    합친 목록에서는 꺼낼 수 없다. 호기별 원본 결과에서 가져온다.
    """
    for e in (evals or []):
        for i in getattr(e.get('result'), 'analysis_items', ()) or ():
            if i.item_name == PRECEDENT_ITEM:
                return i.raw or {}
    return {}


def _precedents(doc, evals, merged) -> None:
    """
    검토 지역의 실제 인허가 사례 — **허가·보류·취소를 함께** 낸다.

    ■ 왜 표로 따로 내는가
      종합평가 표의 '주요 결과' 칸은 한 줄 요약을 담는 자리다. 사례는 사업명·
      용량·일자·사유가 건마다 달라 한 칸에 넣으면 읽히지 않는다.

    ■ ⚠️ 점수에 넣지 않는다
      사례로 허가 확률을 만들 수 없다. 조례에 걸려 애초에 접은 사업은 공시
      자료에 남지 않고(생존 편향), 실제로 사업을 가르는 주민 수용성·지주
      확보·계통 여유는 공시 자료에 없다. 그래서 **사실만 늘어놓고 판단은
      읽는 사람에게 맡긴다.** 이 문단을 지우지 말 것 — 표만 남으면 다음
      사람이 "선례가 많으니 되겠다"로 읽는다.

    ■ 허가만 싣지 않는다
      허가 사례만 세면 "허가만 받으면 된다"는 그림이 된다. 같은 지역의
      보류·부결 사유와, 허가를 받고도 취소된 사업을 나란히 싣는다.
    """
    from . import korec_cases

    item = _precedent_item(merged) or {}
    raw = _precedent_raw(evals)
    sido, sigungu = raw.get('sido') or '', raw.get('sigungu') or ''
    source = raw.get('source') or ''
    if not (sigungu and source):
        report_style._para(doc, item.get('reason') or '사례를 대조하지 못했습니다.')
        return

    c = korec_cases.lookup(sido, sigungu, source)
    label = {'SOLAR': '태양광', 'WIND': '풍력'}.get(source, source)

    _callout(doc, '이 절은 판정·점수에 반영하지 않습니다',
             '아래는 전기위원회가 실제로 심의·처분한 사실입니다. 사례가 많다고 '
             '허가 가능성이 높은 것은 아닙니다 — 조례에 걸려 애초에 접은 사업은 '
             '공시 자료에 남지 않고, 실제로 사업을 가르는 주민 수용성·지주 확보·'
             '계통 여유는 공시 자료에 없습니다. 확률이 아니라 **대조할 사실**로 '
             '보십시오.')

    # ── 허가 사례 ─────────────────────────────────────────────────────
    doc.add_heading('%s %s 허가 사례 (3MW 초과)' % (sigungu, label), level=2)
    permits = c.get('permits') or []
    if permits:
        _styled_table(
            doc, ['허가일', '설비용량', '사업자', '발전소 위치', '사업준비기간'],
            [[p.get('permit_date') or '-',
              ('%s MW' % p['capacity_mw']) if p.get('capacity_mw') else '-',
              (p.get('company') or '-')[:20],
              (p.get('location') or '-')[:44],
              p.get('ready_until') or '-'] for p in permits[:12]],
            widths=[2.0, 1.8, 3.2, 6.4, 2.4])
        if len(permits) > 12:
            _note(doc, '※ 최근 12건만 실었습니다. 전체는 '
                       'data/korec/전기위원회_사례.xlsx 를 보십시오.')
    else:
        _note(doc, '※ %s에는 3MW 초과 %s 허가 사례가 대장에 없습니다%s. '
                   '선례가 없다는 것이 불가하다는 뜻은 아니지만, 규모·절차의 '
                   '전례가 없어 협의가 길어질 수 있습니다.'
              % (sigungu, label,
                 (' (같은 시·도에는 %d건)' % c['sido_count'])
                 if c.get('sido_count') else ''))

    # ── 보류·부결·조건부 ──────────────────────────────────────────────
    blocked = (c.get('blocked') or []) + (c.get('conditional') or [])
    if blocked:
        doc.add_heading('보류·부결·조건부 사례 — 왜 그렇게 됐는가', level=2)
        _styled_table(
            doc, ['회차', '의결', '안건', '사유'],
            [[('제%s차' % b['round']) if b.get('round') else '-',
              b.get('verdict') or '-', (b.get('title') or '')[:40],
              (b.get('reason') or '사유 미기재')[:160]]
             for b in blocked[:10]],
            status_col=None, widths=[1.6, 2.2, 5.0, 7.2])
        _note(doc, '※ 사유는 전기위원회 개최결과 원문이며, 회의록에 실린 위원 '
                   '발언요지까지는 엑셀의 「회차별 안건」에 있습니다. '
                   '이 표는 안건명에 든 **지역 이름**으로 골라낸 것이므로, '
                   '같은 이름의 시·군이 둘 이상인 경우(고성군 등) 다른 지역 '
                   '건이 섞일 수 있습니다 — 사업명을 확인하고 쓰십시오.')

    # ── 허가취소 ──────────────────────────────────────────────────────
    canc = [x for x in (c.get('cancelled') or []) if x['kind'] == 'DISPOSAL']
    hear = [x for x in (c.get('cancelled') or []) if x['kind'] == 'HEARING']
    if canc or hear:
        doc.add_heading('허가를 받고도 좌초한 사업', level=2)
        _styled_table(
            doc, ['구분', '처분일', '사업명', '사업자', '용량', '사유'],
            [[('취소 처분' if x['kind'] == 'DISPOSAL' else '청문 예고'),
              x.get('disposed_on') or '-', (x.get('name') or '')[:26],
              (x.get('company') or '-')[:16],
              ('%s MW' % x['capacity_mw']) if x.get('capacity_mw') else '-',
              (x.get('reason') or '-')[:60]]
             for x in (canc + hear)[:10]],
            widths=[1.8, 2.0, 4.2, 2.8, 1.6, 4.6])
        _callout(doc, '허가는 시작이지 끝이 아닙니다',
                 '이 지역에서 허가를 받고도 준비기간 안에 착공하지 못해 취소된 '
                 '사업이 %d건 있습니다. 사업준비기간과 공사계획인가기간을 '
                 '사업 일정에 반드시 반영하십시오.' % len(canc)
                 if canc else
                 '취소가 예고돼 청문이 진행 중인 사업이 %d건 있습니다.' % len(hear))

    if c.get('built_at'):
        _note(doc, '※ 사례 자료 기준일 %s · 출처 전기위원회(korec.go.kr) '
                   '허가대장·개최결과·회의록·허가취소 공고.' % c['built_at'])


def _parcels(doc, parcel: dict, Pt) -> None:
    """
    검토한 필지의 지번·지목·면적.

    보고서는 유통되는 문서다. '3.26 ha를 검토했다'만 적으면 그 면적이
    **어느 필지들의 합인지** 되짚을 수 없고, 몇 달 뒤 다른 필지를 검토한
    문서와 구분되지 않는다. 지번을 본문에 싣는 이유다.
    """
    rows = []
    for i, x in enumerate(parcel.get('parcels') or [], start=1):
        note = []
        if x.get('jimok') in _PUBLIC_JIMOK:
            note.append('공공용지 지목')
        if not x.get('exact', True):
            note.append('경계 인접 — 확인 필요')
        rows.append((str(i), x.get('addr') or x.get('jibun') or x.get('pnu', ''),
                     x.get('jimok', ''), _fmt_area(x.get('area_m2') or 0),
                     ' · '.join(note) or '-'))
    if rows:
        _styled_table(doc, ['No', '소재지', '지목', '면적', '비고'], rows)

    by = parcel.get('by_jimok') or {}
    if by:
        doc.add_heading('지목 구성', level=3)
        _styled_table(doc, ['지목', '필지 수', '면적'],
                      [(k, f'{v["count"]}필', _fmt_area(v['area_m2']))
                       for k, v in by.items()])

    # 아래 셋은 각주가 아니라 본문에 적는다 — 표에서 면적만 발췌돼 인용되는
    # 순간 단서가 떨어져 나가기 때문이다.
    pub = [x for x in (parcel.get('parcels') or [])
           if x.get('jimok') in _PUBLIC_JIMOK]
    if pub:
        area = sum(x.get('area_m2') or 0 for x in pub)
        _callout(doc, '공공용지 성격 지목이 합계 면적에 포함돼 있습니다',
                 '%s(%d필지 · %s)는 발전부지로 쓸 수 없는 것이 일반적입니다. '
                 '이 보고서의 검토 면적에는 그대로 들어가 있으므로, 사업 규모를 '
                 '잡을 때는 빼고 보십시오.'
                 % (' · '.join(dict.fromkeys(x['jimok'] for x in pub)),
                    len(pub), _fmt_area(area)),
                 tone='CONDITIONAL')

    if parcel.get('inexact'):
        _callout(doc, '경계 근처를 지정해 인접 필지를 채택한 건이 있습니다',
                 '클릭 지점이 어느 필지 안에도 들지 않아 가장 가까운 필지를 '
                 '골랐습니다(%d필지). 의도한 필지가 맞는지 지번으로 확인하십시오.'
                 % len(parcel['inexact']), tone='UNKNOWN')

    misses = parcel.get('misses') or []
    if misses:
        no_parcel = [m for m in misses if m.get('reason') == 'NO_PARCEL']
        fetch = [m for m in misses if m.get('reason') == 'FETCH']
        body = []
        if no_parcel:
            body.append('%d곳은 연속지적에 필지가 없어(도로·하천 등) '
                        '검토에서 제외했습니다.' % len(no_parcel))
        if fetch:
            body.append('%d곳은 조회에 실패했습니다 — 필지가 없는 것이 아니라 '
                        '물어보지 못한 것이며, 그만큼 검토 면적이 실제보다 '
                        '작습니다.' % len(fetch))
        _callout(doc, '지정했으나 검토에 들어가지 못한 지점이 있습니다',
                 ' '.join(body), tone='IMPOSSIBLE' if fetch else 'UNKNOWN')


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
def _item_of(res, name: str):
    return next((i for i in res.analysis_items if i.item_name == name), None)


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
            _sig(o.grade),
            '%d건' % bad, '%d건' % cond,
            _short(_item_of(r, WIND_ITEM)), _short(_item_of(r, GRID_ITEM)),
        ])
    _styled_table(doc, ['호기', '위치', '판정', '불가', '조건부', '풍황', '계통'],
                  rows, accent='7C5BB5', widths=[1.5, 5.4, 2.2, 1.3, 1.5, 2.4, 1.9])


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


def _summary_map(doc, result: dict, layout, Cm, WD_ALIGN_PARAGRAPH,
                 map_image: bytes | None = None) -> None:
    """
    요약지도 — **첫 장 맨 위**에 둔다.

    인허가 실무 검토서는 예외 없이 지도를 문서 맨 앞에 둔다. 판정 표와
    점수는 지도를 읽고 난 다음에야 뜻이 선다 — "조건부 42%"라는 숫자보다
    "어디가 조건부인가"가 먼저 와야 한다.

    map_image가 있으면 그것을 그대로 쓴다 — 화면(SitePicker)을 직접 캡처한
    이미지다. 서버에서 matplotlib로 다시 그린 지도는 정부 원본 데이터의
    단순화나 필지 경계 처리 방식 차이로 화면과 미세하게 달라 보일 수 있는데
    (좁은 물길 근처 등), 화면을 그대로 캡처하면 그 문제 자체가 성립하지
    않는다 — 사용자가 실제로 본 그 그림이 그대로 보고서에 들어간다.
    """
    # ⚠️ Emu를 인자로 받지 않는다. 종전에는 이 함수가 `Emu(...)`를 쓰면서
    #    스코프에 들여오지 않아 **PART 1 제약도가 통째로 실패**했다
    #    (`name 'Emu' is not defined` → '요약지도를 생성하지 못했습니다').
    #    예외를 삼키는 자리라 로그를 보지 않으면 드러나지 않았다.
    from docx.shared import Emu

    import hashlib
    g0 = result.get('geoms') or {}
    logger.info('지도지문 | %-22s | %-6s | png=%s',
                'PART1 제약도', '캡처' if map_image else '서버렌더',
                hashlib.md5(map_image).hexdigest()[:12] if map_image else '-')
    if map_image:
        try:
            doc.add_picture(io.BytesIO(map_image),
                            width=Emu(report_style.image_width_emu(doc)))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception as e:                                  # noqa: BLE001
            logger.warning('캡처 지도 삽입 실패: %s', e)
        return

    g = result.get('geoms') or {}
    screening = result.get('screening') or {}
    parcels = screening.get('parcels') if not screening.get('too_wide') else None
    try:
        if parcels:
            # 태양광은 화면(SitePicker)이 필지 단위로 채색한다. 구역 단위
            # 규제 레이어(연속 면)로 다시 그리면 정부 원본 데이터의 단순화
            # 때문에 좁은 물길·경계 부근에서 화면과 미세하게 어긋나 보인다
            # — 화면과 **같은 필지 도형**으로 그려 그 불일치를 없앤다.
            png = maps.parcel_constraint_map(
                g.get('site_outline') or g.get('area'), parcels,
                turbines=_turbine_points(layout),
                ordinance_house=g.get('ordinance_house'),
                ordinance_road=g.get('ordinance_road'),
                ordinance_road_uncertain=g.get('ordinance_road_uncertain'),
                grandfathered=bool(g.get('ordinance_grandfathered')),
                road_detail=g.get('road_detail'))
        else:
            png = maps.constraint_map(
                g.get('site_outline') or g.get('area'),
                g.get('blocked'), g.get('conditional'), g.get('free'),
                turbines=_turbine_points(layout),
                ordinance_house=g.get('ordinance_house'),
                ordinance_road=g.get('ordinance_road'),
                ordinance_road_uncertain=g.get('ordinance_road_uncertain'),
                grandfathered=bool(g.get('ordinance_grandfathered')),
                road_detail=g.get('road_detail'))
        logger.info('지도지문 | %-22s | %-6s | png=%s', 'PART1 제약도(렌더)',
                    '서버렌더', hashlib.md5(png).hexdigest()[:12])
        doc.add_picture(io.BytesIO(png),
                        width=Emu(report_style.image_width_emu(doc)))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception as e:                                      # noqa: BLE001
        logger.warning('제약도 생성 실패: %s', e)
        doc.add_paragraph('※ 요약지도를 생성하지 못했습니다. 배경지도 조회 실패일 수 있습니다.')

