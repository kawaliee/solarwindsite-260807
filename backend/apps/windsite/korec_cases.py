"""
사례 대조 조회
---------------------------------------------------------------
검토 중인 지역에 **실제로 어떤 사업이 있었는가**를 답한다. 전기위원회 자료
(`korec.py`)를 `sync_korec`이 간추려 둔 `data/korec/cases.json`을 읽는다.

⚠️ **점수·판정에 반영하지 않는다.** 상태를 POSSIBLE로 고정해 점수 산식에서
빼는 이유는 `PrecedentProvider`에 적었다. 요약하면, 사례로 허가 확률을 만들 수
없기 때문이다 — 접은 사업이 모집단에서 빠져 있고(생존 편향), 실제로 사업을
가르는 변수(주민 수용성·지주 확보·계통 여유)가 공시 자료에 없다.

■ 세 갈래를 함께 본다

    허가       그 지역에서 실제로 허가된 사업 — 규모의 선례
    보류·부결   왜 안 됐는가 — **허가 사례보다 쓸모 있는 경우가 많다**
    허가취소    허가를 받고도 좌초한 사업 — 허가가 끝이 아니라는 사실

허가만 보여주면 "허가만 받으면 된다"는 그림이 된다. 셋을 함께 보여준다.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

CASES_PATH = Path(settings.BASE_DIR) / 'data' / 'korec' / 'cases.json'

#: 시·군·구 이름에서 접미사를 뗀 어간. 개최결과 안건명에는 '홍성 태양광
#: 발전사업'처럼 **접미사 없이** 실리기 때문이다.
_SUFFIX = re.compile(r'(시|군|구)$')


@lru_cache(maxsize=1)
def _load() -> dict:
    """
    간추린 사례 자료. 없으면 빈 자료를 돌려준다.

    파일이 없다고 예외를 올리지 않는 까닭은, 사례 대조가 **없어도 검토는
    돌아가야 하는 참고 항목**이기 때문이다. 대신 판정에 "자료 없음"을
    밝혀 사용자가 왜 비었는지 알 수 있게 한다.
    """
    try:
        return json.loads(CASES_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        logger.info('사례 자료가 없습니다 (%s). sync_korec을 먼저 돌리십시오.',
                    CASES_PATH)
        return {}
    except Exception as exc:                               # noqa: BLE001
        logger.warning('사례 자료를 읽지 못했습니다: %s', exc)
        return {}


def available() -> bool:
    return bool(_load().get('register'))


def built_at() -> str:
    return _load().get('built_at', '')


def lookup(sido: str, sigungu: str, source: str) -> dict:
    """
    지역·에너지원별 사례. → 아래 열쇠를 가진 사전

        permits      허가 사례 (최초허가만) — 최근 것부터
        agenda       의결군별 건수 {가결·조건부·보류·부결}
        blocked      보류·부결 사례 — 사유 포함
        cancelled    허가취소 사례
        sido_count   같은 시·도의 허가 사례 수 (시·군·구에 없을 때 쓸 보조선)

    ■ 왜 시·군·구로 좁히는가
      규제의 큰 몫이 **지자체 조례**다. 옆 군에서 됐다는 사실은 이 군에서도
      된다는 근거가 아니다. 그래서 시·군·구를 첫째 열쇠로 삼고, 거기에
      사례가 없을 때만 시·도 수를 곁들인다.
    """
    data = _load()
    if not data:
        return {}

    # ⚠️ 안건은 **이름으로만** 고른다. 개최결과 안건명에는 시·도가 없어
    #    '고성'처럼 같은 이름의 시·군이 둘이면 갈라낼 수가 없다. 그래서 보고서
    #    문구를 '같은 지역 이름으로'라고 적고, 표 아래에 그 한계를 밝힌다.
    #    허가대장·허가취소는 주소가 있어 시·도까지 맞춘다.
    stem = _SUFFIX.sub('', sigungu or '')
    permits = [r for r in data.get('register', ())
               if r['sigungu'] == sigungu and r['source'] == source]
    permits.sort(key=lambda r: r.get('permit_date') or '', reverse=True)

    agenda = [a for a in data.get('agenda', ())
              if a['source'] == source and stem and stem in a['title']]
    cancelled = [c for c in data.get('cancel', ())
                 if c['sigungu'] == sigungu and c['source'] == source]
    cancelled.sort(key=lambda c: c.get('disposed_on') or '', reverse=True)

    counts: dict[str, int] = {}
    for a in agenda:
        counts[a['group']] = counts.get(a['group'], 0) + 1

    return {
        'sido': sido, 'sigungu': sigungu, 'source': source,
        'permits': permits,
        'permit_count': len(permits),
        'max_mw': max((r['capacity_mw'] or 0 for r in permits), default=0.0),
        'total_mw': round(sum(r['capacity_mw'] or 0 for r in permits), 1),
        'agenda_counts': counts,
        # 보류·부결은 사유가 있는 것만 앞세운다 — 사유 없는 건은 읽을 게 없다
        'blocked': sorted(
            [a for a in agenda if a['group'] in ('보류', '부결')],
            key=lambda a: (bool(a.get('reason')), a.get('round') or 0),
            reverse=True),
        'conditional': [a for a in agenda if a['group'] == '조건부'],
        'cancelled': cancelled,
        'sido_count': sum(1 for r in data.get('register', ())
                          if r['sido'] == sido and r['source'] == source),
        'built_at': data.get('built_at', ''),
    }
