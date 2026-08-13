"""
장시간 작업의 협조적 취소
---------------------------------------------------------------
구역 보고서는 호기마다 62개 항목을 조회하므로 첫 생성이 5~10분 걸린다.
그 사이 배치를 고치고 싶으면 멈출 수 있어야 한다.

■ 왜 프런트에서 fetch를 끊는 것만으로는 부족한가

Django의 동기 뷰는 클라이언트가 끊어도 그 사실을 모른 채 끝까지 실행한다.
화면은 풀리지만 외부 API 호출은 그대로 소진된다. Overpass는 이미 사용량
제한(504)을 낸 적이 있어, 헛돈 호출이 다음 시도를 더 막는다.

그래서 서버가 스스로 멈춰야 한다. 작업 중간중간 취소 플래그를 확인하고,
서 있으면 Cancelled를 올려 남은 조회를 하지 않는다.

■ 협조적 취소의 한계 — 즉시 멈추지 않는다

확인 지점 사이에서만 멈춘다. 이미 나간 HTTP 요청 하나는 타임아웃까지
기다린 뒤에야 반환되므로, 최악의 경우 수십 초가 더 걸린다. 요청을 강제로
끊으려면 작업을 별도 프로세스로 옮겨야 하는데, 그 구조 변경이 이 기능이
주는 이득보다 크다.

■ 플래그를 Redis에 두는 이유

취소 요청과 실행 중인 작업이 같은 스레드에 있으리라는 보장이 없다.
프로세스 안 변수로 두면 워커가 늘어나는 순간 취소가 먹지 않는다.
"""
from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

#: 취소 플래그 수명(초). 작업 최대 소요보다 넉넉해야 하지만, 남아 있으면
#: 같은 id를 재사용할 때 즉시 취소돼 버리므로 지나치게 길어도 안 된다.
TTL = 60 * 30

_PREFIX = 'windsite:cancel:'
_PROGRESS_PREFIX = 'windsite:progress:'


def set_progress(job_id: str, done: int, total: int, stage: str = '') -> None:
    """
    진행 상황을 남긴다. 화면이 폴링으로 읽는다.

    작업이 동기 뷰 안에서 도는 동안에는 응답을 흘려보낼 수 없다. 그래서
    진행률을 별도 채널(Redis)에 써 두고 화면이 따로 물어본다.
    """
    if not job_id:
        return
    try:
        cache.set(f'{_PROGRESS_PREFIX}{job_id}',
                  {'done': done, 'total': max(total, 1), 'stage': stage}, TTL)
    except Exception:                                           # noqa: BLE001
        pass


def get_progress(job_id: str) -> dict:
    if not job_id:
        return {}
    try:
        return cache.get(f'{_PROGRESS_PREFIX}{job_id}') or {}
    except Exception:                                           # noqa: BLE001
        return {}


class Cancelled(Exception):
    """사용자가 중단을 요청했다."""


def _key(job_id: str) -> str:
    return f'{_PREFIX}{job_id}'


def request_cancel(job_id: str) -> None:
    if not job_id:
        return
    try:
        cache.set(_key(job_id), True, TTL)
    except Exception:                                           # noqa: BLE001
        logger.warning('취소 플래그 저장 실패 %s', job_id)


def is_cancelled(job_id: str) -> bool:
    if not job_id:
        return False
    try:
        return bool(cache.get(_key(job_id)))
    except Exception:                                           # noqa: BLE001
        # 캐시가 죽었다고 작업을 멈추면 안 된다. 취소가 안 되는 것이
        # 작업이 안 되는 것보다 낫다.
        return False


def check(job_id: str) -> None:
    """취소가 요청됐으면 Cancelled를 올린다. 작업 중간중간 부른다."""
    if is_cancelled(job_id):
        raise Cancelled(job_id)


def clear(job_id: str) -> None:
    """작업이 끝나면 지운다. 남겨두면 같은 id의 다음 작업이 즉시 취소된다."""
    if not job_id:
        return
    try:
        cache.delete(_key(job_id))
        cache.delete(f'{_PROGRESS_PREFIX}{job_id}')
    except Exception:                                           # noqa: BLE001
        pass
