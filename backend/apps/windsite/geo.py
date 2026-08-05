"""
공간연산 유틸리티
---------------------------------------------------------------
위경도(EPSG:4326)는 각도 단위라 거리·면적을 직접 계산할 수 없다.
국내 표준 투영좌표계인 **EPSG:5179(UTM-K)** 로 변환한 뒤 미터 단위로 계산한다.

  - distance_m()  : 지점 ↔ 도형 최근접 거리(m). 도형 내부면 0.
  - area_m2()     : 도형 면적(㎡)
  - intersects()  : 교차 여부

의존성(shapely·pyproj)이 설치되지 않은 환경에서도 앱이 기동되도록
import 실패를 흡수하고, 실제 호출 시점에 명확한 오류를 낸다.
"""
from __future__ import annotations

import functools
import math
from typing import Any

try:                                                            # pragma: no cover
    from pyproj import Transformer
    from shapely.geometry import Point, shape
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import transform as shapely_transform
    GEO_AVAILABLE = True
    GEO_IMPORT_ERROR = ''
except Exception as _e:                                         # noqa: BLE001
    Transformer = None                                          # type: ignore[assignment]
    Point = shape = BaseGeometry = shapely_transform = None     # type: ignore[assignment]
    GEO_AVAILABLE = False
    GEO_IMPORT_ERROR = f'{type(_e).__name__}: {_e}'

#: 대한민국 통합 투영좌표계 (UTM-K). 전국 단일 좌표계라 시·도 경계를 넘어도 일관됨.
METRIC_CRS = 'EPSG:5179'
GEOGRAPHIC_CRS = 'EPSG:4326'


class GeoUnavailable(RuntimeError):
    """공간연산 라이브러리가 없을 때"""


def _require() -> None:
    if not GEO_AVAILABLE:
        raise GeoUnavailable(
            'shapely/pyproj가 설치되지 않아 공간연산을 수행할 수 없습니다. '
            f'requirements.txt 반영 후 이미지를 재빌드하십시오. ({GEO_IMPORT_ERROR})'
        )


@functools.lru_cache(maxsize=2)
def _transformer(src: str, dst: str):
    _require()
    return Transformer.from_crs(src, dst, always_xy=True)


def to_metric(geom: Any):
    """WGS84 도형 → UTM-K 도형"""
    _require()
    t = _transformer(GEOGRAPHIC_CRS, METRIC_CRS)
    return shapely_transform(t.transform, geom)


def point_metric(lat: float, lng: float):
    """위경도 → UTM-K 점"""
    _require()
    x, y = _transformer(GEOGRAPHIC_CRS, METRIC_CRS).transform(lng, lat)
    return Point(x, y)


def to_geographic_xy(x: float, y: float) -> tuple[float, float]:
    """UTM-K → (lng, lat)"""
    _require()
    return _transformer(METRIC_CRS, GEOGRAPHIC_CRS).transform(x, y)


def geom_from_geojson(geojson: dict | None):
    """GeoJSON geometry dict → shapely 도형 (없으면 None)"""
    if not geojson:
        return None
    _require()
    try:
        g = shape(geojson)
    except Exception:                                           # noqa: BLE001
        return None
    if g.is_empty:
        return None
    # 자기교차 폴리곤 보정 — 공공 데이터에 종종 존재한다
    if not g.is_valid:
        g = g.buffer(0)
    return g if not g.is_empty else None


# ----------------------------------------------------------------------
def distance_m(site_metric, geom_metric) -> float:
    """지점 ↔ 도형 최근접 거리(m). 도형 내부/교차면 0.0"""
    _require()
    return float(site_metric.distance(geom_metric))


def area_m2(geom_metric) -> float:
    _require()
    return float(getattr(geom_metric, 'area', 0.0))


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """투영 없이 쓰는 근사 거리(m) — 라이브러리 부재 시 폴백용"""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def format_distance(m: float) -> str:
    """사람이 읽는 거리 표기 — 근접 판정 문구에 사용"""
    if m <= 0:
        return '중첩(0m)'
    if m < 1000:
        return f'{m:,.0f}m'
    return f'{m / 1000:,.2f}km'


def representative_latlng(geom_metric) -> tuple[float, float] | None:
    """도형의 대표점(내부 보장) → (lat, lng)"""
    _require()
    try:
        p = geom_metric.representative_point()
    except Exception:                                           # noqa: BLE001
        return None
    lng, lat = to_geographic_xy(p.x, p.y)
    return lat, lng
