"""
인허가 사례 대조
---------------------------------------------------------------
검토 지역에서 **실제로 무슨 일이 있었는가**를 보고서에 싣는다. 전기위원회
자료(3MW 초과 허가대장·개최결과·회의록·허가취소 공고)를 지역과 에너지원으로
좁혀 보여준다.

■ ⚠️ 점수에 넣지 않는다 — 왜인가

사례를 세어 "허가 확률 72%"를 내고 싶어지지만, 그 숫자는 근거가 없다.

  1. **생존 편향** — 공시 자료에는 신청된 것만 있다. 조례 이격에 걸려 애초에
     접은 사업이 가장 많은데 그 모집단이 통째로 빠져 있다. 개최결과가 부결·
     보류를 담아 이를 덜어 줄 뿐, 없애지는 못한다.
  2. **가른 변수가 자료에 없다** — 주민 수용성, 지자체장 의지, 지주 확보,
     계통 여유가 실제로 사업을 가르는데 공시 자료에 없다. 관측 가능한 것
     (용도지역·이격·경사도)만으로 만든 유사도는 허가 확률이 아니다.
  3. **표본 부족** — 조례는 지자체마다 다른데 지자체당 사례는 몇 건이다.
  4. **자기실현적 왜곡** — "확률 30%"를 보고 접으면 그 사업은 신청되지 않아
     다음 통계에서도 빠진다. 시스템이 스스로 데이터를 편향시킨다.

그래서 상태를 `POSSIBLE`로 고정한다. 점수 산식은 판정 **상태**로만 움직이므로
이렇게 두면 사례가 점수를 흔들지 않는다(`CurtailmentInfoProvider`와 같은 얼개).

■ 허가만 보여주지 않는다

허가 사례만 세면 "허가만 받으면 된다"는 그림이 된다. 세 갈래를 함께 싣는다.

    허가       그 지역에서 실제로 허가된 사업 — 규모의 선례
    보류·부결   왜 안 됐는가 — 허가 사례보다 쓸모 있는 경우가 많다
    허가취소    허가를 받고도 좌초한 사업 — 허가가 끝이 아니라는 사실
"""
from __future__ import annotations

import logging

from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

#: 보고서에 이름을 들어 보일 사례 수. 다 늘어놓으면 읽히지 않는다.
SHOW_PERMITS = 4
SHOW_BLOCKED = 3
SHOW_CANCELLED = 3

_SRC_LABEL = {'SOLAR': '태양광', 'WIND': '풍력'}


class PrecedentProvider(LayerProvider):
    """
    인허가 사례 대조 — **참고 표기 전용**.

    상태를 POSSIBLE로 고정한다. 까닭은 이 파일 머리말에 적었다.
    """

    category = '사업성'
    item_name = '인허가 사례 대조 (참고)'
    data_source = '전기위원회 허가대장·개최결과·회의록·허가취소 공고'
    required_settings = ()
    default_law = '해당 없음 (참고 정보)'

    def __init__(self, sido: str = '', sigungu: str = '', energy: str = 'WIND'):
        self.sido = sido
        self.sigungu = sigungu
        self.energy = energy

    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import korec_cases

        label = _SRC_LABEL.get(self.energy, self.energy)
        if not korec_cases.available():
            return self.item(
                status=Status.POSSIBLE,
                reason='사례 자료가 적재되어 있지 않아 대조하지 못했습니다. '
                       '`python manage.py sync_korec`으로 전기위원회 자료를 '
                       '내려받으면 이 항목에 지역별 실제 사례가 실립니다. '
                       '**판정·점수에 반영하지 않는 참고 항목입니다.**',
                difficulty=Difficulty.LOW, confidence=Confidence.LOW,
                raw={'available': False})

        if not self.sigungu:
            return self.item(
                status=Status.POSSIBLE,
                reason='행정구역을 확인하지 못해 사례를 대조하지 못했습니다. '
                       '**판정·점수에 반영하지 않는 참고 항목입니다.**',
                difficulty=Difficulty.LOW, confidence=Confidence.LOW,
                raw={'available': True, 'sigungu': ''})

        c = korec_cases.lookup(self.sido, self.sigungu, self.energy)
        return self.item(
            status=Status.POSSIBLE,
            reason=_narrate(c, label),
            difficulty=Difficulty.LOW,
            confidence=Confidence.MEDIUM if c.get('permit_count') else Confidence.LOW,
            source_url='https://www.korec.go.kr',
            action_required=_action(c, label),
            raw={
                # 보고서의 사례 절이 이 셋으로 다시 조회한다. 판정 결과를
                # 합치는 단계(merge_items)에서 raw가 떨어져 나가므로,
                # 보고서는 호기별 결과에서 이 값을 꺼내 쓴다.
                'sido': self.sido, 'sigungu': self.sigungu,
                'source': self.energy,
                'permit_count': c.get('permit_count', 0),
                'max_mw': c.get('max_mw', 0), 'total_mw': c.get('total_mw', 0),
                'agenda_counts': c.get('agenda_counts', {}),
                'blocked': len(c.get('blocked', ())),
                'cancelled': len(c.get('cancelled', ())),
                'built_at': c.get('built_at', ''),
            })


def _narrate(c: dict, label: str) -> str:
    """사례를 문장으로 엮는다. **확률은 만들지 않는다.**"""
    sg = c.get('sigungu') or '해당 시·군·구'
    out: list[str] = []

    # ── 허가 사례 ─────────────────────────────────────────────────────
    n = c.get('permit_count', 0)
    if n:
        out.append(
            f'**{sg}에서 3MW 초과 {label} 발전사업 {n}건이 실제로 허가**되었습니다 '
            f'(합계 {c["total_mw"]:,.1f}MW · 최대 {c["max_mw"]:,.2f}MW). '
            + _permit_list(c['permits']))
    else:
        out.append(
            f'{sg}에는 3MW 초과 {label} 허가 사례가 대장에 없습니다'
            + (f' (같은 시·도에는 {c["sido_count"]}건). ' if c.get('sido_count')
               else '. ')
            + '선례가 없다는 것이 불가하다는 뜻은 아니지만, **규모·절차의 '
              '전례가 없어 협의가 길어질 수 있습니다.**')

    # ── 조건부 ────────────────────────────────────────────────────────
    cond = c.get('conditional') or []
    if cond:
        out.append(
            f'전기위원회에서 **조건부 허가 {len(cond)}건**이 있었습니다. '
            + _reason_of(cond[0]))

    # ── 보류·부결 ─────────────────────────────────────────────────────
    blocked = c.get('blocked') or []
    if blocked:
        kinds = ' · '.join(
            f'{k} {n}건' for k, n in _count(b['verdict'] for b in blocked))
        out.append(
            f'같은 지역 이름으로 **보류·부결 {len(blocked)}건**이 있었습니다'
            f'({kinds}). ' + ' '.join(
                _reason_of(b) for b in blocked[:SHOW_BLOCKED]))

    # ── 허가취소 ──────────────────────────────────────────────────────
    canc = [x for x in (c.get('cancelled') or []) if x['kind'] == 'DISPOSAL']
    hear = [x for x in (c.get('cancelled') or []) if x['kind'] == 'HEARING']
    if canc:
        more = (f' 외 {len(canc) - SHOW_CANCELLED}건'
                if len(canc) > SHOW_CANCELLED else '')
        out.append(
            f'**허가를 받고도 취소된 사업이 {len(canc)}건** 있습니다 — '
            + ' · '.join(
                f'{x["name"]}({x["capacity_mw"] or "?"}MW, {x["disposed_on"]})'
                for x in canc[:SHOW_CANCELLED])
            + f'{more}. 준비기간 안에 착공하지 못한 것이 주된 사유입니다. '
              '**허가는 시작이지 끝이 아닙니다.**')
    if hear:
        out.append(f'취소 청문이 예고된 사업도 {len(hear)}건 있습니다.')

    out.append(
        '**이 항목은 판정·점수에 반영하지 않습니다.** 사례는 확률이 아닙니다 — '
        '조례에 걸려 애초에 접은 사업은 공시 자료에 남지 않고(생존 편향), '
        '주민 수용성·지주 확보·계통 여유처럼 실제로 사업을 가르는 조건은 '
        '공시 자료에 없습니다.')
    if c.get('built_at'):
        out.append(f'(사례 자료 기준일 {c["built_at"]})')
    return ' '.join(out)


def _permit_list(permits: list[dict]) -> str:
    if not permits:
        return ''
    head = ' · '.join(
        f'{p["permit_date"] or "일자미상"} {p["capacity_mw"] or "?"}MW'
        f'({p["location"][:22]}…)' if len(p['location']) > 22
        else f'{p["permit_date"] or "일자미상"} {p["capacity_mw"] or "?"}MW'
             f'({p["location"]})'
        for p in permits[:SHOW_PERMITS])
    more = (f' 외 {len(permits) - SHOW_PERMITS}건'
            if len(permits) > SHOW_PERMITS else '')
    return f'최근 사례: {head}{more}.'


#: 사유 한 줄의 길이 상한. 원문 사유가 길게 이어지는 회차가 있어(제254차 등)
#: 그대로 실으면 사례 목록이 통째로 사유 하나에 묻힌다. 전문은 엑셀에 있다.
REASON_CHARS = 110


def _reason_of(a: dict) -> str:
    """왜 그렇게 됐는지. 사유가 없으면 안건명만 밝힌다 — 지어내지 않는다."""
    who = f'제{a["round"]}차 「{a["title"][:40]}」'
    why = (a.get('reason') or '').strip()
    if not why:
        return f'{who}(사유 미기재).'
    why = _clip(why)
    return f'{who} — {why.rstrip(" .·")}.'


def _clip(why: str) -> str:
    """
    긴 사유를 줄인다. **문장이 끝나는 자리에서 끊는다.**

    글자 수로 자르면 「…자료가 미등재된…」처럼 말이 중간에 끊긴 채 문서로
    유통된다. 원문 인용이라 통째로 줄일 수는 없으므로, 상한 안에서 마지막
    마침표까지만 쓰고 그래도 넘치면 어절 경계에서 끊는다.
    """
    if len(why) <= REASON_CHARS:
        return why
    cut = max(why.rfind('다. ', 0, REASON_CHARS), why.rfind('. ', 0, REASON_CHARS))
    if cut > REASON_CHARS * 0.4:
        return why[:cut + 1].rstrip()
    cut = why.rfind(' ', 0, REASON_CHARS)
    return (why[:cut] if cut > REASON_CHARS * 0.4 else why[:REASON_CHARS]).rstrip()


def _count(values) -> list[tuple[str, int]]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return sorted(out.items(), key=lambda x: -x[1])


def _action(c: dict, label: str) -> str:
    sg = c.get('sigungu') or '해당 시·군·구'
    if not c.get('permit_count'):
        return (f'{sg}에 3MW 초과 {label} 선례가 없습니다. 유사 규모를 허가받은 '
                f'인근 시·군의 조례·협의 경과를 확인하고, 사전 협의를 이르게 '
                f'시작하십시오.')
    blocked = c.get('blocked') or []
    if blocked:
        return (f'같은 지역의 보류·부결 사유가 이 사업에도 해당하는지 대조하십시오 '
                f'— 특히 주민 수용성과 계통 연계 여건은 회차가 바뀌어도 되풀이해 '
                f'지적되는 항목입니다. 원문은 '
                f'`data/korec/전기위원회_사례.xlsx`의 「회차별 안건」에 있습니다.')
    return ('허가 선례의 설비용량·사업준비기간을 이 사업의 계획과 대조하십시오. '
            '원문은 `data/korec/전기위원회_사례.xlsx`에 있습니다.')
