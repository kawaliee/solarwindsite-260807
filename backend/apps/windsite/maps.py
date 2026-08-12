"""
검토 보고서용 지도 렌더링
---------------------------------------------------------------
V-World 실측 배경지도(위성영상·일반지도) 위에 검토에 실제로 사용한
**벡터 데이터**를 겹쳐 그린다.

좌표는 전 구간 EPSG:5179(UTM-K)로 통일하므로 축척이 미터 단위로 정확하다.

⚠️ 배경지도를 재투영 없이 깔 수 있는 이유 (2026-08 실측)
   V-World 정적 이미지 API가 `crs=EPSG:5179`를 그대로 받는다. 반환 이미지는
   요청한 center를 정중앙에 두고 zoom별로 아래 지상해상도를 갖는다.

       z13 15.0 · z14 7.5 · z15 3.75 · z16 1.875 · z17 0.9375 · z18 0.46875 m/px
       → mpp(z) = 0.46875 × 2^(18-z)

   가로·세로 모두 동일함을 상관분석으로 확인했다(x 1.887 / y 1.887 m/px @z16).
   따라서 축 좌표계(5179)와 이미지가 1:1로 맞아 오차 없이 겹칠 수 있다.
   웹메르카토르 타일을 쓰면 위도 37°에서 약 1.25배 축척 왜곡이 생겨 축척 막대가
   틀어지는데, 이 방식은 그 문제가 없다.

⚠️ 배경은 **판정 근거가 아니다.** 판정은 벡터 데이터로만 하고, 배경은 위치를
   눈으로 확인하기 위한 참고다. 각 지도 하단에 그 취지를 명시한다.

배경지도 조회에 실패하면 배경 없이 벡터만 그린다 — 보고서 생성을 막지 않는다.

산출: PNG bytes — report.py가 docx에 삽입한다.
"""
from __future__ import annotations

import io
import logging

from . import geo, httpcache

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# V-World 정적 지도 이미지 (배경)
VWORLD_IMAGE_URL = 'https://api.vworld.kr/req/image'
#: 실측 상한 — 1024까지 허용, 1280부터 거부된다
MAX_IMAGE_PX = 1024
#: zoom 18의 지상해상도(m/px). 한 단계 낮아질 때마다 2배가 된다.
BASE_MPP_AT_Z18 = 0.46875
MIN_ZOOM, MAX_ZOOM = 7, 18

#: basemap 파라미터 — 실측 확인값. HYBRID·MIDNIGHT은 이 API에서 거부된다.
STYLE_SATELLITE = 'PHOTO_HYBRID'   # 위성영상 + 지명·경계 라벨
STYLE_PLAIN = 'GRAPHIC'            # 일반지도


def _pick_zoom(span_m: float, px: int) -> tuple[int, float]:
    """이미지가 span_m를 덮는 가장 상세한 zoom과 그때의 m/px."""
    for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        mpp = BASE_MPP_AT_Z18 * (2 ** (18 - z))
        if px * mpp >= span_m:
            return z, mpp
    return MIN_ZOOM, BASE_MPP_AT_Z18 * (2 ** (18 - MIN_ZOOM))


def _basemap(center, extent_m: float, style: str):
    """
    (이미지 배열, (xmin, xmax, ymin, ymax)) 또는 실패 시 None.
    반환 extent는 EPSG:5179 미터 좌표라 축에 그대로 얹을 수 있다.
    """
    from django.conf import settings

    key = getattr(settings, 'VWORLD_API_KEY', '')
    if not key:
        return None

    z, mpp = _pick_zoom(extent_m * 2, MAX_IMAGE_PX)
    params = {
        'service': 'image', 'request': 'getmap', 'version': '2.0',
        'format': 'png', 'basemap': style, 'crs': 'EPSG:5179',
        'center': f'{center.x:.2f},{center.y:.2f}', 'zoom': str(z),
        'size': f'{MAX_IMAGE_PX},{MAX_IMAGE_PX}',
        'key': key, 'domain': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
    }

    def fetch() -> bytes:
        import httpx
        r = httpx.get(VWORLD_IMAGE_URL, params=params, timeout=60.0,
                      headers={'User-Agent': 'windsite-report/1.0'})
        r.raise_for_status()
        if r.content[:4] != b'\x89PNG':
            raise RuntimeError('배경지도 응답이 이미지가 아닙니다')
        return r.content

    try:
        blob = httpcache.get_or_set('vworld_image', params, fetch)
        if isinstance(blob, str):            # 캐시가 문자열로 돌려주는 경우 방어
            return None
        from PIL import Image
        import numpy as np
        img = np.asarray(Image.open(io.BytesIO(blob)).convert('RGB'))
    except Exception:                                           # noqa: BLE001
        logger.warning('배경지도 조회 실패 — 배경 없이 그립니다', exc_info=True)
        return None

    half = MAX_IMAGE_PX * mpp / 2
    return img, (center.x - half, center.x + half,
                 center.y - half, center.y + half)

#: 지목별 색상 — 지적 현황도
JIMOK_COLORS = {
    '임야': '#2f6b3a', '잡종지': '#7a8b3d', '목장용지': '#4c8b52',
    '전': '#c98a2b', '답': '#b8752a', '과수원': '#a86a2f',
    '대': '#8c4b6b', '도로': '#666b73', '하천': '#2f6b8b', '구거': '#3f7b96',
    '제방': '#5a6b7a', '철도용지': '#4a4a55', '묘지': '#6b5a4a',
}
DEFAULT_JIMOK_COLOR = '#9aa1ab'

#: 규제 유형별 색상 — 규제 중첩도
REGULATION_COLOR = '#c0392b'
PROXIMITY_COLOR = '#d68910'
SITE_COLOR = '#1f77d0'


def _configure_font() -> str:
    """
    한글 폰트 지정. 컨테이너에 fonts-nanum이 설치되어 있어야 한다.
    폰트가 없으면 글자가 □로 깨지므로, 찾지 못하면 경고를 남긴다.
    """
    import matplotlib
    from matplotlib import font_manager

    candidates = ['NanumGothic', 'NanumBarunGothic', 'Malgun Gothic',
                  'AppleGothic', 'Noto Sans CJK KR', 'Noto Sans KR']
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams['font.family'] = name
            matplotlib.rcParams['axes.unicode_minus'] = False
            return name
    logger.warning('한글 폰트를 찾지 못했습니다 — 지도의 한글이 깨질 수 있습니다. '
                   'Dockerfile에 fonts-nanum 설치가 필요합니다.')
    matplotlib.rcParams['axes.unicode_minus'] = False
    return ''


def _new_axes(title: str, extent_m: float, center, basemap: str = STYLE_SATELLITE):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    _configure_font()
    fig, ax = plt.subplots(figsize=(8, 8), dpi=140)
    ax.set_title(title, fontsize=13, pad=12)

    bg = _basemap(center, extent_m, basemap) if basemap else None
    if bg is not None:
        img, ext = bg
        # origin='upper' — 이미지 첫 행이 북쪽이다. extent가 5179 미터라 축과 1:1로 맞는다.
        ax.imshow(img, extent=ext, origin='upper', zorder=0,
                  interpolation='bilinear')
        ax._ws_has_basemap = True
    else:
        ax._ws_has_basemap = False

    ax.set_xlim(center.x - extent_m, center.x + extent_m)
    ax.set_ylim(center.y - extent_m, center.y + extent_m)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color('#c9ced6')
    return fig, ax


def _has_bg(ax) -> bool:
    return bool(getattr(ax, '_ws_has_basemap', False))


def _halo(n: int = 3):
    """배경지도 위 글자가 묻히지 않도록 흰 테두리를 준다."""
    from matplotlib import patheffects
    return [patheffects.withStroke(linewidth=n, foreground='white')]


def _draw_site(ax, center, radius_m: int | None = None):
    ax.plot([center.x], [center.y], marker='*', markersize=18,
            color=SITE_COLOR, zorder=10, label='검토 지점',
            markeredgecolor='white', markeredgewidth=1.2)
    if radius_m:
        from matplotlib.patches import Circle
        ax.add_patch(Circle((center.x, center.y), radius_m, fill=False,
                            edgecolor=SITE_COLOR, linestyle='--', linewidth=1.8,
                            zorder=9, label=f'검토 반경 {radius_m:,}m',
                            path_effects=_halo(3.5)))


def _draw_scalebar(ax, extent_m: float):
    """축척 막대 — 미터 단위 좌표계라 그대로 그릴 수 있다."""
    # 검토 반경이 100m까지 내려가므로 잔단위까지 둔다.
    # 없으면 '54m' 같은 어중간한 값이 찍힌다.
    for unit in (5000, 2000, 1000, 500, 200, 100, 50, 20, 10):
        if unit <= extent_m * 0.6:
            bar = unit
            break
    else:
        bar = max(5, int(extent_m * 0.4))

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    sx = x0 + (x1 - x0) * 0.06
    sy = y0 + (y1 - y0) * 0.06
    ax.plot([sx, sx + bar], [sy, sy], color='#111417', linewidth=3.5, zorder=12,
            path_effects=_halo(5), solid_capstyle='butt')
    label = f'{bar / 1000:g}km' if bar >= 1000 else f'{bar}m'
    ax.text(sx + bar / 2, sy + (y1 - y0) * 0.012, label,
            ha='center', va='bottom', fontsize=9.5, color='#111417', zorder=12,
            fontweight='bold', path_effects=_halo(3))


def _plot_geom(ax, g, **kw):
    """shapely 도형을 축에 그린다 (Polygon/MultiPolygon/LineString 대응)."""
    from shapely.geometry import (
        GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon,
    )

    if isinstance(g, (MultiPolygon, MultiLineString, GeometryCollection)):
        for part in g.geoms:
            _plot_geom(ax, part, **kw)
        return
    if isinstance(g, Polygon):
        xs, ys = g.exterior.xy
        ax.fill(xs, ys, **kw)
        return
    if isinstance(g, LineString):
        xs, ys = g.xy
        line_kw = {k: v for k, v in kw.items()
                   if k in ('color', 'linewidth', 'zorder', 'alpha', 'label')}
        ax.plot(xs, ys, **line_kw)


def _outline(ax, g, **kw):
    """폴리곤 경계선만 그린다 — 배경지도가 비치도록 채움 위에 얹는다."""
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

    if isinstance(g, (MultiPolygon, GeometryCollection)):
        for part in g.geoms:
            _outline(ax, part, **kw)
        return
    if isinstance(g, Polygon):
        xs, ys = g.exterior.xy
        ax.plot(xs, ys, **kw)


def _finish(fig, legend: bool = True) -> bytes:
    import matplotlib.pyplot as plt

    ax = fig.axes[0]
    # 배경 출처 표기 — 배경은 참고이지 판정 근거가 아니라는 점을 함께 남긴다
    if _has_bg(ax):
        ax.text(0.995, 0.005,
                '배경: 국토교통부 V-World (참고용, 판정 근거 아님)',
                transform=ax.transAxes, ha='right', va='bottom',
                fontsize=7, color='#111417', zorder=13, path_effects=_halo(2.5))
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        seen: dict[str, object] = {}
        for h, l in zip(handles, labels):
            seen.setdefault(l, h)
        if seen:
            ax.legend(seen.values(), seen.keys(), loc='upper right',
                      fontsize=8, framealpha=0.92, facecolor='white',
                      edgecolor='#c9ced6')
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format='png')
    plt.close(fig)
    return buf.getvalue()


# ======================================================================
def cadastral_map(lat: float, lng: float, radius_m: int, features: list[dict]) -> bytes:
    """
    지적 현황도 — 필지 폴리곤을 지목별로 색칠한다.
    features: [{'geom': shapely(metric), 'jimok': str}]
    """
    center = geo.point_metric(lat, lng)
    extent = radius_m * 1.35
    fig, ax = _new_axes('지적 현황도 (지목별)', extent, center)

    # 배경 위성영상이 비치도록 채움을 옅게 하고 경계선으로 필지를 구분한다
    alpha = 0.42 if _has_bg(ax) else 0.65
    for f in features:
        c = JIMOK_COLORS.get(f['jimok'], DEFAULT_JIMOK_COLOR)
        _plot_geom(ax, f['geom'], color=c, alpha=alpha, zorder=3,
                   label=f['jimok'], linewidth=0.3)
    for f in features:
        _outline(ax, f['geom'], color='#ffffff', linewidth=0.5, alpha=0.75, zorder=4)

    _draw_site(ax, center, radius_m)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def regulation_map(lat: float, lng: float, radius_m: int, layers: list[dict]) -> bytes:
    """
    규제 중첩도 — 저촉/근접한 규제 구역만 표시한다.
    layers: [{'name': str, 'geoms': [shapely(metric)], 'overlapping': bool}]
    """
    center = geo.point_metric(lat, lng)
    extent = radius_m * 2.2
    fig, ax = _new_axes('규제 구역 중첩도', extent, center)

    alpha = 0.28 if _has_bg(ax) else 0.35
    for lyr in layers:
        color = REGULATION_COLOR if lyr.get('overlapping') else PROXIMITY_COLOR
        for g in lyr['geoms']:
            _plot_geom(ax, g, color=color, alpha=alpha, zorder=3,
                       label=lyr['name'], linewidth=0.6)
            _outline(ax, g, color=color, linewidth=1.2, alpha=0.95, zorder=4)

    _draw_site(ax, center, radius_m)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def surroundings_map(lat: float, lng: float, radius_m: int,
                     buildings: list, roads: list) -> bytes:
    """주변 현황도 — 건물·도로 (이격거리 판단의 시각적 근거)"""
    center = geo.point_metric(lat, lng)
    extent = max(radius_m * 2.5, 1200)
    fig, ax = _new_axes('주변 현황도 (건물·도로)', extent, center)
    if not buildings and not roads and _has_bg(ax):
        ax.text(0.5, 0.97, '조회 반경 내 건물·도로 벡터자료 없음 (배경 영상만 표시)',
                transform=ax.transAxes, ha='center', va='top', fontsize=9,
                color='#111417', zorder=13, path_effects=_halo(3))

    road_c = '#ffd54a' if _has_bg(ax) else '#8a9099'
    bldg_c = '#e0554a' if _has_bg(ax) else '#5a6270'
    for g in roads:
        _plot_geom(ax, g, color=road_c, linewidth=1.4, zorder=2, label='도로')
    for g in buildings:
        _plot_geom(ax, g, color=bldg_c, alpha=0.75, zorder=3,
                   label='건물', linewidth=0.2)

    _draw_site(ax, center, radius_m)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def setback_map(lat: float, lng: float, rings: list[dict],
                facilities: list[dict]) -> bytes:
    """
    이격거리 동심원도 — 조례 이격거리 반경과 정온시설 위치.
    rings: [{'target': str, 'distance_m': int}]
    facilities: [{'name','lat','lng','distance_m'}]
    """
    from matplotlib.patches import Circle

    center = geo.point_metric(lat, lng)
    max_r = max([r['distance_m'] for r in rings] + [1000])
    extent = max_r * 1.3
    fig, ax = _new_axes('이격거리 동심원 분석', extent, center)

    palette = ['#c0392b', '#d68910', '#8e44ad', '#16a085', '#2c3e50']
    for i, r in enumerate(sorted(rings, key=lambda x: -x['distance_m'])):
        c = palette[i % len(palette)]
        ax.add_patch(Circle((center.x, center.y), r['distance_m'], fill=False,
                            edgecolor=c, linewidth=2.0, zorder=4,
                            label=f'{r["target"]} {r["distance_m"]:,}m',
                            path_effects=_halo(3.5)))

    for f in facilities:
        p = geo.point_metric(f['lat'], f['lng'])
        inside = any(f['distance_m'] <= r['distance_m'] for r in rings)
        ax.plot([p.x], [p.y], marker='o', markersize=7, zorder=6,
                color='#c0392b' if inside else '#4a4f57',
                markeredgecolor='white', markeredgewidth=1.0,
                label='기준 내 정온시설' if inside else '정온시설')

    _draw_site(ax, center)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def constraint_map(area, blocked, conditional, free,
                   turbines: list | None = None) -> bytes:
    """
    사업구역 제약도 — 배제·조건부·제약없음을 색으로 나눠 위성영상 위에 얹는다.

    도형은 모두 EPSG:5179 shapely 객체다. 제약이 강한 쪽을 위에 쌓아,
    겹치는 지점에서 더 엄한 판정이 보이게 한다.
    """
    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    # 구역이 화면에 꽉 차지 않도록 15% 여백을 둔다
    extent = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes('사업구역 제약도', extent, center)

    for g, color, label in ((free, '#2e9e2e', '제약 없음'),
                            (conditional, '#e8a33d', '조건부'),
                            (blocked, '#d9363e', '배제')):
        if g is None or g.is_empty:
            continue
        _plot_geom(ax, g, color=color, alpha=0.45 if _has_bg(ax) else 0.65,
                   zorder=3, label=label)
        _outline(ax, g, color=color, linewidth=0.8, zorder=4)

    _outline(ax, area, color='#111111', linewidth=2.0, zorder=6)

    for i, p in enumerate(turbines or [], start=1):
        ax.plot(p.x, p.y, marker='o', markersize=7, color='#ffffff',
                markeredgecolor='#111111', markeredgewidth=1.2, zorder=7)
        ax.annotate(str(i), (p.x, p.y), fontsize=8, fontweight='bold',
                    ha='center', va='center', zorder=8,
                    path_effects=_halo(2) if _has_bg(ax) else None)

    _draw_scalebar(ax, extent)
    return _finish(fig)
