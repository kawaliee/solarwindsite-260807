"""
수치표고모델(DEM) 판독 — 평균경사도·사면향
---------------------------------------------------------------
태양광 이격거리 조례에는 거리가 아닌 **지형 조건**을 규정한 조항이 섞여 있다.

    홍성군 [별표 26] 가.4)  "경사도가 15도 이상인 산지에 입지하지 아니할 것"

거리 추출기로는 잡히지 않는 종류라 표고 격자가 따로 필요하다. V-World
토지특성의 '지형지세'(급경사·고지)는 **공시지가 산정용 구분**이라 이 판정에
쓸 수 없다 — 조례가 말하는 평균경사도와 다른 지표다.

■ 원본은 파일이다

국내 공공포털은 DEM을 API가 아니라 **파일로** 배포한다. 좌표를 넣으면
표고를 돌려주는 공개 서비스가 없다. 그래서 내려받은 도엽을 `data/dem/`
아래 두고 여기서 직접 읽는다.

■ 없는 정밀도를 있는 척하지 않는다

국토지리정보원 **공개DEM은 90m 격자**다(5m는 공개 대상이 아니다). 태양광
부지가 200~300m 규모면 격자가 3~9칸뿐이라, 조례 기준 언저리에서는 평균값을
단정할 수 없다. 그래서 이 모듈은 값과 함께 **표본 칸 수와 커버리지**를 돌려주고,
판정하는 쪽이 경계 사례를 '확인 필요'로 남길 수 있게 한다.

도엽이 없는 구역은 0도로 채우지 않는다. 자료가 없다는 사실을 그대로 돌려준다 —
경사가 없는 땅과 표고를 모르는 땅은 전혀 다르다.
"""
from __future__ import annotations

import glob
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

#: 원본 파일을 두는 곳. 출처별 하위 폴더는 자유롭게 나눠도 된다(재귀 탐색).
DEM_DIR = os.path.join(settings.BASE_DIR, 'data', 'dem')

#: 읽을 확장자. IMG는 국토지리정보원 공개DEM, TIF는 Copernicus 계열.
EXTENSIONS = ('*.img', '*.tif', '*.tiff', '*.asc')

#: 표본이 이보다 적으면 평균을 신뢰하지 않는다.
#: 90m 격자에서 4칸이면 한 변 180m — 이보다 작은 필지는 값이 한두 칸에 좌우된다.
MIN_CELLS = 4

#: 남향 판정 범위(방위각, 도). 정남 180°를 중심으로 ±45°.
SOUTH_RANGE = (135.0, 225.0)


class DemUnavailable(RuntimeError):
    """표고 자료가 없거나 읽지 못했다. '경사가 0'과 구분하기 위한 예외."""


_index: list[dict] | None = None
_lock = threading.Lock()


def _files() -> list[str]:
    out: list[str] = []
    for ext in EXTENSIONS:
        out += glob.glob(os.path.join(DEM_DIR, '**', ext), recursive=True)
    return sorted(out)


def index(refresh: bool = False) -> list[dict]:
    """
    보유 도엽 목록. [{path, bounds, crs, cell_m, name}, …]

    파일을 열어 경계를 읽으므로 한 번만 만들고 재사용한다. 도엽을 추가한
    뒤에는 refresh=True로 다시 만든다(운영 중 파일이 늘어난다).
    """
    global _index
    if _index is not None and not refresh:
        return _index
    with _lock:
        if _index is not None and not refresh:
            return _index
        try:
            import rasterio
        except ImportError:                                     # pragma: no cover
            logger.warning('rasterio 미설치 — DEM을 읽을 수 없습니다.')
            _index = []
            return _index

        rows: list[dict] = []
        for p in _files():
            try:
                with rasterio.open(p) as s:
                    rows.append({
                        'path': p,
                        'bounds': tuple(s.bounds),
                        'crs': str(s.crs) if s.crs else '',
                        'cell_m': abs(s.transform.a),
                        'name': os.path.basename(p),
                    })
            except Exception:                                   # noqa: BLE001
                logger.warning('DEM 판독 실패 %s', p, exc_info=True)
        _index = rows
        logger.info('DEM 도엽 %d장 색인', len(rows))
        return _index


def _covering(geom_metric) -> list[dict]:
    """도형과 겹치는 도엽. 좌표계가 다른 파일은 쓰지 않는다(추측 변환 금지)."""
    from . import geo
    minx, miny, maxx, maxy = geom_metric.bounds
    want = geo.METRIC_CRS.upper()
    hits = []
    for t in index():
        if t['crs'].upper() != want:
            continue
        bx0, by0, bx1, by1 = t['bounds']
        if bx1 < minx or bx0 > maxx or by1 < miny or by0 > maxy:
            continue
        hits.append(t)
    return hits


def stats(geom_metric) -> dict:
    """
    도형 안의 경사도·사면향 통계.

    반환::

        {'mean_deg', 'max_deg', 'p90_deg',       경사도(도)
         'south_ratio',                          남향(135~225°) 셀 비율
         'mean_elev_m',                          평균 표고
         'cells',                                표본 셀 수
         'cell_m',                               격자 간격(m)
         'coverage',                             도형 대비 자료가 있는 비율
         'tiles'}                                사용한 도엽 파일명

    :raises DemUnavailable: 도엽이 없거나 도형 안에 유효 표고가 없을 때
    """
    import numpy as np
    from rasterio.features import geometry_mask
    from rasterio.merge import merge as rio_merge
    import rasterio

    tiles = _covering(geom_metric)
    if not tiles:
        raise DemUnavailable('검토 구역을 덮는 표고 도엽이 없습니다.')

    # 도형 주변으로 한 칸 넉넉히 잡는다. 경계에서 기울기를 구하려면
    # 바깥 셀이 하나는 있어야 한다(np.gradient가 가장자리를 한쪽 차분으로 낸다).
    pad = max(t['cell_m'] for t in tiles) * 2
    minx, miny, maxx, maxy = geom_metric.bounds
    win = (minx - pad, miny - pad, maxx + pad, maxy + pad)

    srcs = []
    try:
        for t in tiles:
            srcs.append(rasterio.open(t['path']))
        arr, transform = rio_merge(srcs, bounds=win)
        nodata = srcs[0].nodata
    finally:
        for s in srcs:
            try:
                s.close()
            except Exception:                                   # noqa: BLE001
                pass

    z = arr[0].astype('float64')
    if nodata is not None:
        z[z == nodata] = np.nan
    if z.size == 0 or np.all(np.isnan(z)):
        raise DemUnavailable('해당 구역의 표고 값이 모두 비어 있습니다.')

    cell = abs(transform.a)
    # np.gradient는 (행=y, 열=x) 순서다. y는 위로 갈수록 좌표가 커지지만
    # 배열은 위쪽이 첫 행이라 부호가 뒤집힌다 — 사면향을 낼 때 이걸 틀리면
    # 남향과 북향이 통째로 바뀐다.
    dzdy, dzdx = np.gradient(z, cell, cell)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))

    # 도형 안쪽만 본다. all_touched로 걸치는 칸까지 포함한다 —
    # 90m 격자에서 작은 필지는 완전히 포함되는 칸이 하나도 없을 수 있다.
    mask = geometry_mask([geom_metric], out_shape=z.shape, transform=transform,
                         invert=True, all_touched=True)
    sel = mask & ~np.isnan(slope)
    cells = int(sel.sum())
    if cells == 0:
        raise DemUnavailable('검토 구역 안에 유효한 표고 격자가 없습니다.')

    sl = slope[sel]
    # 사면향 — 북쪽 0°에서 시계방향. 평지(기울기 0)는 향이 없으므로 뺀다.
    asp = (np.degrees(np.arctan2(-dzdx, dzdy)) + 360.0) % 360.0
    flat = np.hypot(dzdx, dzdy) < 1e-9
    a = asp[sel & ~flat]
    south = float(((a >= SOUTH_RANGE[0]) & (a <= SOUTH_RANGE[1])).mean()) if a.size else 0.0

    # 커버리지 — 도형 면적 중 표고 자료가 있는 비율. 도엽 경계에 걸친 구역은
    # 절반만 보고 평균을 낸 것일 수 있어, 값과 함께 반드시 내보낸다.
    want = int(mask.sum())
    coverage = (cells / want) if want else 0.0

    return {
        'mean_deg': round(float(sl.mean()), 1),
        'max_deg': round(float(sl.max()), 1),
        'p90_deg': round(float(np.percentile(sl, 90)), 1),
        'south_ratio': round(south, 3),
        'mean_elev_m': round(float(np.nanmean(z[sel])), 1),
        'cells': cells,
        'cell_m': round(cell, 1),
        'coverage': round(coverage, 3),
        'reliable': cells >= MIN_CELLS and coverage >= 0.9,
        'tiles': [t['name'] for t in tiles],
    }


def summary() -> dict:
    """보유 현황 — 연동 현황 화면과 운영 점검용."""
    rows = index()
    cells = sorted({r['cell_m'] for r in rows})
    return {
        'tile_count': len(rows),
        'cell_sizes_m': cells,
        'crs': sorted({r['crs'] for r in rows}),
        'dir': DEM_DIR,
    }


def bearing_label(deg: float) -> str:
    """방위각 → 8방위 이름."""
    names = ('북', '북동', '동', '남동', '남', '남서', '서', '북서')
    return names[int((deg % 360) / 45.0 + 0.5) % 8]
