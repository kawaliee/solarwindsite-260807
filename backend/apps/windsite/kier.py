"""
KIER 풍력 시공간 자원정보 (공공데이터포털)
---------------------------------------------------------------
한국에너지기술연구원이 만든 1km급 풍력자원 격자 데이터다. ASOS 관측소는
부지에서 수십 km 떨어져 있는 반면 이쪽은 격자가 부지 위에 있어, 고도별·
방위별 분포를 보는 데 쓸모가 있다.

  GET https://apis.data.go.kr/B551184/WindPwService/getWindPwHrInfo
      serviceKey · pageNo · numOfRows · lat · lon · alti · azi · type

■ 판정에 쓰지 않는다

삼척 능선 격자에서 120m 값이 0.99m/s로 나온다. 같은 지점 ASOS 환산값
(1.9~2.3m/s)의 절반이고, 육상풍력 기준선 5.5m/s와는 비교가 되지 않는다.
국내 어느 능선도 연평균 1m/s일 수 없다.

상세기능 이름이 '**1시간단위** 풍력 데이터'인 점, 그런데 시각을 지정하는
파라미터가 없는 점을 함께 보면 **특정 시각의 순간 풍속**으로 판단된다.
그래서 이 값으로 사업성을 판정하지 않고 참고 수치로만 싣는다.
판정은 ASOS 연평균 기반([[providers.wind]])이 계속 담당한다.

■ 실측으로 확인한 함정 세 가지 (2026-08)

  · **좌표 끝자리 0을 붙이면 0건이 온다.** lat=37.27은 660건,
    lat=37.2700은 0건. 격자 키를 문자열로 맞추는 것으로 보인다
  · **numOfRows는 50 이하만 유효하다.** 51 이상이면 totalCount조차 없는
    빈 응답이 온다 — 오류가 아니라 정상 응답처럼 보여서 '데이터 없음'으로
    오인하기 쉽다
  · **_type=json을 주면 XML이 온다.** 파라미터 이름은 type이고, 아무것도
    주지 않으면 JSON이 온다

■ 응답

  660건 = 격자 11점 × 고도 5종(40·80·120·160·200m) × 방위각 12종(30°~360°)
  항목: alti · azi · lat · lon · wind(m/s)
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections import defaultdict

from django.conf import settings

from . import httpcache
from .providers.base import LayerProvider

logger = logging.getLogger(__name__)

BASE = 'https://apis.data.go.kr/B551184/WindPwService'
OP = 'getWindPwHrInfo'

#: 한 번에 받을 수 있는 최대 건수. 51 이상은 빈 응답이 온다(실측).
PAGE_SIZE = 50
#: 격자 11점 × 고도 5 × 방위 12 = 660건. 여유를 두고 끊는다.
MAX_PAGES = 16

#: 좌표 소수 자릿수.
#:
#: 이 API는 좌표를 **접두어로 매칭**한다. 자릿수를 늘릴수록 범위가 좁아진다.
#:   36.7  / 129.2   → 592,740건 (0.1° 격자 전체)
#:   36.67 / 129.17  →   5,940건 (0.01°)
#:   37.272 / 129.235 →     60건 (격자 1점 = 고도 5 × 방위 12)
#:   36.669 / 129.169 →      0건 (그 격자에 데이터가 없음)
#:
#: 3자리 이상은 격자에 정확히 맞지 않으면 0건이 되는데, 어느 격자가 존재하는지
#: 미리 알 수 없다. 2자리로 조회해 그 안의 격자를 받은 뒤 부지 인근만 골라 쓴다.
COORD_DIGITS = 2

#: 2자리 조회는 수천 건이 온다. 페이지를 끝까지 넘기면 50건씩 100회가 넘어
#: 보고서 생성 시간에 그대로 얹히므로, 인근 격자를 고를 만큼만 받고 끊는다.
MAX_SAMPLES = 600


def _coord(v: float) -> str:
    """
    37.27159 → '37.27'.

    끝자리 0이 남으면 접두어가 달라져 0건이 된다 ('37.20' ≠ '37.2').
    """
    s = f'{float(v):.{COORD_DIGITS}f}'.rstrip('0').rstrip('.')
    return s or '0'


def fetch(lat: float, lng: float) -> list[dict]:
    """격자점의 고도별·방위별 풍속 전체. 실패하면 빈 목록."""
    key = getattr(settings, 'KIER_API_KEY', '') or getattr(settings, 'DATA_GO_KR_KEY', '')
    if not key:
        return []

    def call() -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                'serviceKey': key,
                # type을 주지 않으면 JSON이 오는데, XML이 파싱이 더 안정적이라
                # _type=json으로 일부러 XML을 받는다(이 API는 이름과 반대로 동작한다).
                '_type': 'json',
                'pageNo': str(page),
                'numOfRows': str(PAGE_SIZE),
                'lat': _coord(lat),
                'lon': _coord(lng),
            }
            res = LayerProvider.get(f'{BASE}/{OP}', params, timeout=60.0)
            res.raise_for_status()
            try:
                root = ET.fromstring(res.text)
            except ET.ParseError:
                break
            items = [{c.tag: c.text for c in i} for i in root.iter('item')]
            if not items:
                break
            out.extend(items)
            total = root.findtext('.//totalCount')
            if len(out) >= MAX_SAMPLES or (total and len(out) >= int(total)):
                break
        return out

    try:
        return httpcache.get_or_set(
            'kier_wind', {'lat': _coord(lat), 'lon': _coord(lng)}, call)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('KIER 풍황 조회 실패 %s,%s: %s', lat, lng, e)
        return []


def summarize(rows: list[dict], lat: float | None = None,
              lng: float | None = None, keep_km: float = 3.0) -> dict:
    """
    고도별 평균과 방위별 평균으로 요약한다.

    lat/lng를 주면 그 지점에서 keep_km 안의 격자만 쓴다. 0.01° 조회에는
    부지에서 1km 넘게 떨어진 격자도 섞여 온다. 반대로 격자 하나를 콕 집는
    것은 1km 해상도를 넘는 해석이라 하지 않고, 주변만 남겨 평균한다.
    """
    if not rows:
        return {}
    used_km = None
    if lat is not None and lng is not None:
        near = []
        for r in rows:
            try:
                d = _haversine_km(lat, lng, float(r['lat']), float(r['lon']))
            except (KeyError, TypeError, ValueError):
                continue
            if d <= keep_km:
                near.append(r)
        if near:
            rows, used_km = near, keep_km
    by_alt: dict[int, list] = defaultdict(list)
    by_azi: dict[int, list] = defaultdict(list)
    for r in rows:
        try:
            w = float(r['wind'])
            by_alt[int(r['alti'])].append(w)
            by_azi[int(r['azi'])].append(w)
        except (KeyError, TypeError, ValueError):
            continue
    if not by_alt:
        return {}

    def avg(v):
        return sum(v) / len(v)

    alt = {k: round(avg(v), 2) for k, v in sorted(by_alt.items())}
    azi = {k: round(avg(v), 2) for k, v in sorted(by_azi.items())}
    top = max(azi, key=azi.get) if azi else None
    return {
        'by_altitude_ms': alt,
        'radius_km': used_km,
        'by_azimuth_ms': azi,
        # 방위별 평균이 가장 큰 섹터 — 주풍향의 단서다. 순간값 기반이므로
        # 연간 주풍향으로 단정하지 않는다.
        'dominant_azimuth_deg': top,
        'grid_points': len({(r.get('lat'), r.get('lon')) for r in rows}),
        'samples': len(rows),
        'source': 'KIER 풍력 시공간 자원정보 (공공데이터포털)',
        'caveat': ('시각을 지정하는 파라미터가 없고 값의 크기가 연평균으로 보기에 '
                   '너무 낮아 순간 풍속으로 판단됩니다. 판정에 쓰지 않고 고도·방위 '
                   '분포를 보는 참고 수치로만 싣습니다.'),
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
