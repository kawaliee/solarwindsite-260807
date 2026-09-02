"""
해역 판정 — 육상풍력 유효지역에서 바다를 뺀다
---------------------------------------------------------------
「발전사업세부허가기준, 전기요금산정기준, 전력량계허용오차 및 전력계통운영
업무에 관한 고시」는 육상풍력 사업 유효지역을 *신청좌표를 중심으로 반지름
2km인 원 이내로 **해역을 제외한** 지역*으로 정하고, 이어서 *풍력발전기
블레이드의 회전 가능 범위를 수평으로 투영한 면적은 유효지역 이내여야 한다*고
정한다.

그러므로 유효지역 판정에는 축이 둘이다.

  ① 거리   신청좌표~호기 + 로터 반지름 ≤ 2,000m   (rawwind.py)
  ② 해역   블레이드 회전 투영원이 육지 안        (이 모듈)

②를 빼면 해안 능선 사업이 통과한 것처럼 읽힌다. 실측에서 완도 10기는 ①을
자유 좌표로 만족하지만, 로터 200m 기준으로 블레이드 원의 최대 73%가 바다에
걸친다 — ①만 보면 보이지 않는 사실이다.

데이터 — 왜 행정경계를 육지로 쓰는가
  V-World WFS의 읍·면·동 경계(lt_c_ademd)를 육지로 본다. 전용 해안선
  레이어를 쓰지 않는 것은 이미 이 시스템이 쓰고 있는 경계이고(jurisdiction.py),
  실측에서 해안선을 따르는 것이 확인되기 때문이다.

    평창(내륙) 반경 2km 원의 육지 비율  100.0%
    완도(해안) 반경 2km 원의 육지 비율   11.1%

⚠️ 한계 — 이 경계는 **행정 경계**지 조위 기준 해안선이 아니다. 수십 m 어긋날
   수 있고, 간척·매립은 고시 반영에 시차가 있다. 실측에서 완도 7·10호기가
   경계 밖 32.4m·9.5m로 나왔는데, 이 정도 거리는 자료 정밀도 안이라 '바다에
   섰다'고 단정할 수 없다. 그래서 이 모듈은 **판정하지 않고 측정만 하며**,
   결과를 쓰는 쪽이 확인 필요로 다루게 한다.
"""
from __future__ import annotations

import logging

from django.conf import settings
from shapely.geometry import shape
from shapely.ops import unary_union

from . import geo, httpcache
from .providers.base import LayerProvider

logger = logging.getLogger(__name__)

WFS_URL = 'https://api.vworld.kr/req/wfs'

#: 육지로 볼 경계. 시군구(lt_c_adsigg)도 같은 해안선을 그리지만, 읍면동이
#: 조각이 작아 좁은 만(灣)까지 따라간다.
LAND_LAYER = 'lt_c_ademd'

#: 한 번에 받을 최대 경계 피처. 반경 2km + 여유면 해안이라도 수십 건이다.
MAX_FEATURES = 300

#: 경계 자료의 정밀도 한계(m). 이 안쪽 차이는 '바다'라고 말하지 않는다.
#: 완도 실측에서 7·10호기가 경계 밖 32.4m·9.5m로 나왔다 — 행정경계와 조위
#: 기준 해안선의 차이만으로도 이 정도는 생긴다.
BOUNDARY_TOLERANCE_M = 50


class CoastUnavailable(RuntimeError):
    """육지 경계를 받지 못했다. **해역 없음으로 갈음하지 않는다.**"""


def land_union(bounds_geom):
    """
    도형을 덮는 **육지 폴리곤**(EPSG:5179).

    실패하면 예외를 올린다. 조회 실패를 '전부 육지'로 갈음하면 해역 요건을
    통과한 것처럼 읽히는데, 이 시스템에서 조회 안 됨과 제약 없음은 전혀 다른
    사실이다.
    """
    minx, miny, maxx, maxy = bounds_geom.bounds
    params = {
        'SERVICE': 'WFS', 'REQUEST': 'GetFeature', 'VERSION': '1.1.0',
        'KEY': settings.VWORLD_API_KEY,
        'DOMAIN': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
        'TYPENAME': LAND_LAYER,
        'SRSNAME': geo.METRIC_CRS,
        'BBOX': ','.join(f'{v:.2f}' for v in (minx, miny, maxx, maxy)),
        'OUTPUT': 'application/json',
        'MAXFEATURES': str(MAX_FEATURES),
    }

    def call() -> dict:
        res = LayerProvider.get(WFS_URL, params, timeout=60.0)
        res.raise_for_status()
        ct = (res.headers.get('content-type') or '').lower()
        if 'json' not in ct:
            raise CoastUnavailable(
                f'육지 경계 응답이 JSON이 아닙니다 ({ct}): {res.text[:160]}')
        return res.json()

    feats = (httpcache.get_or_set('vworld_wfs', params, call).get('features')
             or [])
    if not feats:
        raise CoastUnavailable('육지 경계 피처가 없습니다.')
    return unary_union([shape(f['geometry']) for f in feats]).buffer(0)


def blade_sea(lat: float, lng: float, rotor_m: float, land=None) -> dict:
    """
    한 호기의 **블레이드 회전 투영원**이 해역에 걸치는 정도.

    반환: {sea_m2, sea_ratio, offshore_m, on_land, tolerant}
      · sea_ratio   블레이드 원 중 육지 밖 비율
      · offshore_m  호기 중심이 육지 밖이면 해안선까지 거리(육지면 0)
      · tolerant    중심이 육지 밖이지만 경계 정밀도(50m) 안이라 단정 불가
    """
    pt = geo.point_metric(lat, lng)
    blade = pt.buffer(rotor_m)
    if land is None:
        land = land_union(blade.buffer(2000))
    sea = blade.difference(land)
    on_land = land.contains(pt)
    off = 0.0 if on_land else float(pt.distance(land))
    return {
        'sea_m2': round(float(sea.area), 1),
        'sea_ratio': round(float(sea.area) / float(blade.area), 4),
        'offshore_m': round(off, 1),
        'on_land': bool(on_land),
        'tolerant': (not on_land) and off <= BOUNDARY_TOLERANCE_M,
    }


def valid_area(center_lat: float, center_lng: float, radius_m: float,
               land=None) -> dict:
    """
    신청좌표 기준 **유효지역**(2km 원 − 해역)의 넓이.

    허가 가능 범위가 실제로 얼마나 되는지를 면적으로 보여 준다. 해안 사업은
    원의 절반 넘게 바다인 경우가 있어, 배치를 넣을 자리 자체가 좁다.
    """
    c = geo.point_metric(center_lat, center_lng)
    circle = c.buffer(radius_m)
    if land is None:
        land = land_union(circle)
    eff = circle.intersection(land)
    return {
        'circle_m2': round(float(circle.area), 1),
        'valid_m2': round(float(eff.area), 1),
        'land_ratio': round(float(eff.area) / float(circle.area), 4),
        'geom': eff,
    }
