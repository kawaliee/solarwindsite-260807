"""
V-World 지오코딩 어댑터
---------------------------------------------------------------
- geocode(address)        : 주소 → 좌표(lat,lng)         [도로명 우선, 실패 시 지번 재시도]
- reverse_geocode(lat,lng): 좌표 → 행정구역(시도/시군구)  [지번 우선]

VWORLD_API_KEY 가 없으면 None 을 반환한다(추측/자동 채움 금지 원칙).
좌표만 입력해도 reverse_geocode 로 행정구역이 채워지면 지자체 이격거리 조례 조회가
자동으로 걸린다.
"""
from __future__ import annotations

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

VWORLD_ADDR_URL = 'https://api.vworld.kr/req/address'
_TIMEOUT = 8.0


def _key() -> str:
    return getattr(settings, 'VWORLD_API_KEY', '') or ''


def _domain() -> str:
    return getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost'


def geocode(address: str) -> dict | None:
    """주소 문자열 → {'lat','lng','matched','type'} 또는 None.

    도로명(road)으로 먼저 시도하고, 결과가 없으면 지번(parcel)으로 재시도한다.
    """
    address = (address or '').strip()
    if not address or not _key():
        return None

    for addr_type in ('road', 'parcel'):
        params = {
            'service': 'address', 'request': 'getcoord', 'version': '2.0',
            'crs': 'epsg:4326', 'address': address, 'type': addr_type,
            'refine': 'true', 'simple': 'false', 'format': 'json',
            'key': _key(), 'domain': _domain(),
        }
        try:
            r = httpx.get(VWORLD_ADDR_URL, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            resp = data.get('response', {})
            if resp.get('status') == 'OK':
                pt = resp['result']['point']
                refined = (resp.get('refined') or {}).get('text') or address
                return {
                    'lat': float(pt['y']), 'lng': float(pt['x']),
                    'matched': refined, 'type': addr_type,
                }
        except Exception as e:                              # noqa: BLE001
            logger.warning('V-World geocode 실패(%s): %s', addr_type, e)
    return None


def reverse_geocode(lat: float, lng: float) -> dict | None:
    """좌표 → {'sido','sigungu','address','structure'} 또는 None."""
    if not _key():
        return None

    params = {
        'service': 'address', 'request': 'getAddress', 'version': '2.0',
        'crs': 'epsg:4326', 'point': f'{lng},{lat}', 'type': 'both',
        'format': 'json', 'key': _key(), 'domain': _domain(),
    }
    try:
        r = httpx.get(VWORLD_ADDR_URL, params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        resp = data.get('response', {})
        if resp.get('status') != 'OK':
            return None
        results = resp.get('result') or []
        if not results:
            return None
        # 지번(parcel) 결과 우선, 없으면 첫 결과
        best = next((x for x in results if x.get('type') == 'parcel'), results[0])
        st = best.get('structure', {}) or {}
        return {
            'sido': st.get('level1', ''),        # 시·도
            'sigungu': st.get('level2', ''),     # 시·군·구
            'address': best.get('text', ''),
            'structure': st,
        }
    except Exception as e:                                  # noqa: BLE001
        logger.warning('V-World reverse geocode 실패: %s', e)
        return None
