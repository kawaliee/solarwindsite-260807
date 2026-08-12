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
import re
from typing import Any

try:                                                            # pragma: no cover
    from pyproj import Transformer
    from shapely.geometry import Point, Polygon, box, shape
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import transform as shapely_transform, unary_union
    GEO_AVAILABLE = True
    GEO_IMPORT_ERROR = ''
except Exception as _e:                                         # noqa: BLE001
    Transformer = None                                          # type: ignore[assignment]
    Point = Polygon = box = shape = BaseGeometry = None         # type: ignore[assignment]
    shapely_transform = unary_union = None                      # type: ignore[assignment]
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


# ----------------------------------------------------------------------
# 사업구역(폴리곤) 연산
#
# 점+반경 검토는 원 하나로 끝나지만, 대규모 육상풍력은 사업구역이 면이다.
# 아래 유틸은 그 면을 다루는 데 필요한 최소 집합이다.
# ----------------------------------------------------------------------

#: V-World 데이터 API가 geomFilter로 받는 polygon/box의 **요청면적 상한**.
#: 서버 실측으로 확인된 값이다. 초과하면 INVALID_RANGE로 거절하며
#: 오류 본문에 "polygon, box경우 요청면적이 10km² 이내"라고 명시된다.
MAX_FILTER_AREA_M2 = 10_000_000

#: 조회 타일 한 변(m). 3,000m = 9km²로 상한에 8.5% 여유를 둔다.
#: 여유가 필요한 이유: 서버는 4326으로 되돌린 도형의 면적을 재는데
#: 그 값이 5179 실면적보다 1~2% 크게 나온다(실측: 16.00km² → 16.26km²).
TILE_SIDE_M = 3000

#: 타일 수 상한. 64장 = 576km²(57,600ha)로, 실제 사업구역을 훨씬 넘는다.
#: 잘못 그린 구역이 수백 회 호출로 번지는 것을 막는 안전장치다.
MAX_TILES = 64

#: 외접원 반경에 얹는 여유 비율. shapely buffer()의 다각형 근사 오차를 덮는다.
#: 기본 분할(quad_segs=8)에서 근사 다각형은 참원보다 최대 약 0.24% 작다.
_CIRCUM_MARGIN = 0.01


def polygon_metric(ring: list) -> Any:
    """[(lat, lng), …] → UTM-K 폴리곤. 3점 미만이면 None."""
    _require()
    pts = [(float(a), float(o)) for a, o in ring]
    if len(pts) < 3:
        return None
    t = _transformer(GEOGRAPHIC_CRS, METRIC_CRS)
    xy = [t.transform(lng, lat) for lat, lng in pts]
    g = Polygon(xy)
    if not g.is_valid:
        g = g.buffer(0)
    return g if not g.is_empty else None


def to_geographic(geom_metric: Any) -> Any:
    """UTM-K 도형 → WGS84 도형"""
    _require()
    t = _transformer(METRIC_CRS, GEOGRAPHIC_CRS)
    return shapely_transform(t.transform, geom_metric)


#: shapely의 .wkt는 'POLYGON ((…' 처럼 타입명 뒤에 공백을 넣는다. 표준 표기지만
#: V-World geomFilter 파서는 이걸 타입 미상으로 보고 거절한다(실측). 공백을 지운
#: 'POLYGON((…' 는 통과한다. 지오메트리 내용과 무관한 순전한 표기 문제다.
_WKT_TYPE_GAP = re.compile(r'^([A-Z]+)\s+\(')


def wkt_4326(geom_metric: Any, precision: int = 7) -> str:
    """
    UTM-K 도형 → geomFilter에 실을 WGS84 WKT.

    좌표 자릿수를 고정하는 건 미관 때문이 아니라 **캐시 키를 안정시키기 위해서**다.
    부동소수 끝자리가 흔들리면 같은 타일이 매번 다른 키가 되어 캐시가 죽는다.
    1e-7도는 약 1.1cm라 필터 정밀도에는 영향이 없다.
    """
    _require()

    def _round(xs, ys, zs=None):
        # shapely는 좌표 배열을 통째로 넘기는 경로를 먼저 시도한다.
        # 스칼라만 받는 함수를 주면 예외를 내고 원소별로 다시 부르는데,
        # 예외에 기대지 않도록 두 경우를 모두 직접 처리한다.
        try:
            return ([round(v, precision) for v in xs],
                    [round(v, precision) for v in ys])
        except TypeError:
            return round(xs, precision), round(ys, precision)

    raw = shapely_transform(_round, to_geographic(geom_metric)).wkt
    return _WKT_TYPE_GAP.sub(r'\1(', raw)


def tiles(geom_metric: Any, side_m: int = TILE_SIDE_M) -> tuple[list, dict]:
    """
    구역을 덮는 격자 타일 목록을 만든다.

    타일은 **구역에 맞춰 자르지 않고 격자에 스냅된 정사각형 그대로** 돌려준다.
    구역 모양대로 자르면 조회량은 조금 줄지만, 구역을 손볼 때마다 필터 도형이
    달라져 캐시가 전부 무효가 된다. 격자에 고정하면 구역을 수정해도 겹치는
    타일은 그대로 재사용된다 — 검토를 반복하는 실사용 패턴에서 이쪽이 훨씬 싸다.

    반환: (타일 폴리곤 목록, meta)
      meta['capped']  타일 수 상한에 걸려 잘렸는지 (True면 결과가 구역 전체를 덮지 않는다)
    """
    _require()
    minx, miny, maxx, maxy = geom_metric.bounds
    i0, i1 = math.floor(minx / side_m), math.floor(maxx / side_m)
    j0, j1 = math.floor(miny / side_m), math.floor(maxy / side_m)

    out = []
    capped = False
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            t = box(i * side_m, j * side_m, (i + 1) * side_m, (j + 1) * side_m)
            # 바운딩박스만 겹치고 실제로는 안 닿는 타일을 걸러 헛호출을 막는다
            if not t.intersects(geom_metric):
                continue
            if len(out) >= MAX_TILES:
                capped = True
                break
            out.append(t)
        if capped:
            break

    return out, {
        'capped': capped,
        'side_m': side_m,
        'tile_area_m2': side_m * side_m,
        'requested': len(out),
    }


def circumscribed(geom_metric: Any) -> tuple[float, float, int]:
    """
    도형을 덮는 원 → (중심 lat, 중심 lng, 반경 m).

    면적 상한이 없는 POINT+buffer 조회로 구역 전체를 한 번에 훑을 때,
    그리고 아직 면을 모르는 어댑터에 대표 지점을 넘길 때 쓴다.
    최소외접원이 아니라 무게중심 기준이라 약간 크지만, 덮는 것이 보장되면 된다.

    반경에 여유(_CIRCUM_MARGIN)를 더한다. shapely의 buffer()는 원을 다각형으로
    근사하는데 그 다각형은 원 **안쪽**에 들어가므로, 딱 맞는 반경으로는
    구역 꼭짓점이 근사 원 밖으로 삐져나온다. 서버 조회는 참원이라 문제없지만,
    이 값으로 포함 검사를 하는 코드가 조용히 틀리는 것을 막는다.
    """
    _require()
    c = geom_metric.centroid
    r = max(c.distance(Point(x, y)) for x, y in _outer_coords(geom_metric))
    lng, lat = to_geographic_xy(c.x, c.y)
    return lat, lng, int(math.ceil(r * (1 + _CIRCUM_MARGIN) + 1))


def _outer_coords(geom_metric: Any):
    """폴리곤/멀티폴리곤의 바깥 경계 좌표를 모두 훑는다."""
    geoms = getattr(geom_metric, 'geoms', None)
    if geoms is not None:
        for g in geoms:
            yield from _outer_coords(g)
        return
    ext = getattr(geom_metric, 'exterior', None)
    if ext is not None:
        yield from ext.coords
    else:
        yield from geom_metric.coords


def rings_4326(geom_metric: Any, precision: int = 6) -> list:
    """
    UTM-K 도형 → 화면에 겹쳐 그릴 [[ [lat,lng], … ], …] 링 목록.

    구멍(내부 링)은 버린다. 제약 영역을 지도에 반투명으로 덮는 용도라
    구멍까지 정확히 그릴 필요가 없고, 링 구조를 단순하게 유지하는 편이
    프런트에서 다루기 쉽다. 면적 수치는 도형 원본으로 계산하므로 영향이 없다.
    """
    if geom_metric is None or geom_metric.is_empty:
        return []
    _require()
    out: list = []
    for part in getattr(geom_metric, 'geoms', [geom_metric]):
        ext = getattr(part, 'exterior', None)
        if ext is None:
            continue
        ring = [
            [round(lat, precision), round(lng, precision)]
            for lng, lat in (to_geographic_xy(x, y) for x, y in ext.coords)
        ]
        if len(ring) >= 4:
            out.append(ring)
    return out


def union(geoms: list) -> Any:
    """도형 합집합. 빈 목록이면 None."""
    _require()
    valid = [g for g in geoms if g is not None and not g.is_empty]
    if not valid:
        return None
    u = unary_union(valid)
    return None if u.is_empty else u


def subtract(base: Any, cutter: Any) -> Any:
    """base − cutter. cutter가 없으면 base 그대로."""
    _require()
    if cutter is None or cutter.is_empty:
        return base
    r = base.difference(cutter)
    return None if r.is_empty else r


def clip(geom: Any, mask: Any) -> Any:
    """geom ∩ mask. 겹치지 않으면 None."""
    _require()
    if geom is None or mask is None:
        return None
    r = geom.intersection(mask)
    return None if r.is_empty else r


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
