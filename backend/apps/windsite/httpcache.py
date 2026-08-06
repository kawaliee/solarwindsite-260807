"""
외부 공간정보 응답 캐시
---------------------------------------------------------------
한 지점을 검토하면 V-World 40여 회 + Overpass 3회를 호출한다. 같은 지점을
다시 보거나 보고서를 만들 때 동일 호출이 그대로 반복돼 지연이 커지고,
Overpass는 사용량 제한(429)에 걸린다.

규제 레이어·지적·변전소 위치는 분 단위로 바뀌지 않으므로 응답을 캐시한다.
캐시 실패는 조회 실패가 아니므로 **예외를 삼키고 원본 호출로 진행**한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

PREFIX = 'windsite:v1:'


def _ttl() -> int:
    return int(getattr(settings, 'WINDSITE_CACHE_TTL', 86400))


def make_key(namespace: str, payload: dict[str, Any]) -> str:
    """호출 파라미터로 안정적인 캐시 키를 만든다 (인증키는 제외)."""
    safe = {k: v for k, v in payload.items()
            if k.lower() not in ('key', 'oc', 'servicekey', 'apikey', 'domain')}
    blob = json.dumps(safe, sort_keys=True, ensure_ascii=False, default=str)
    return PREFIX + namespace + ':' + hashlib.sha1(blob.encode('utf-8')).hexdigest()


def get_or_set(namespace: str, payload: dict[str, Any],
               producer: Callable[[], Any], ttl: int | None = None) -> Any:
    """
    캐시에 있으면 그대로, 없으면 producer()를 호출해 저장 후 반환.

    producer가 예외를 던지면 캐시에 넣지 않고 그대로 전파한다
    (실패 응답을 캐시하면 일시적 오류가 하루 동안 굳는다).
    """
    key = make_key(namespace, payload)
    try:
        hit = cache.get(key)
    except Exception:                                           # noqa: BLE001
        logger.debug('캐시 조회 실패 — 원본 호출로 진행', exc_info=True)
        return producer()

    if hit is not None:
        return hit

    value = producer()
    try:
        cache.set(key, value, ttl if ttl is not None else _ttl())
    except Exception:                                           # noqa: BLE001
        logger.debug('캐시 저장 실패 — 무시', exc_info=True)
    return value


def clear() -> None:
    """전체 캐시 비우기 (레이어 정의를 바꾼 뒤 등)."""
    try:
        cache.clear()
    except Exception:                                           # noqa: BLE001
        logger.warning('캐시 초기화 실패', exc_info=True)
