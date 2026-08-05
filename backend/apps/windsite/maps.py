"""
검토 보고서용 지도 렌더링
---------------------------------------------------------------
배경 타일을 쓰지 않고, 검토에 실제로 사용한 **벡터 데이터만** 그린다.
(타일을 깔면 보기에는 좋지만, 판정 근거가 아닌 그림이 섞여 오해를 부른다)

좌표는 전 구간 EPSG:5179(UTM-K)로 통일하므로 축척이 미터 단위로 정확하다.

산출: PNG bytes — report.py가 docx에 삽입한다.
"""
from __future__ import annotations

import io
import logging

from . import geo

logger = logging.getLogger(__name__)

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


def _new_axes(title: str, extent_m: float, center):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    _configure_font()
    fig, ax = plt.subplots(figsize=(8, 8), dpi=140)
    ax.set_title(title, fontsize=13, pad=12)
    ax.set_xlim(center.x - extent_m, center.x + extent_m)
    ax.set_ylim(center.y - extent_m, center.y + extent_m)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color('#c9ced6')
    return fig, ax


def _draw_site(ax, center, radius_m: int | None = None):
    ax.plot([center.x], [center.y], marker='*', markersize=16,
            color=SITE_COLOR, zorder=10, label='검토 지점')
    if radius_m:
        from matplotlib.patches import Circle
        ax.add_patch(Circle((center.x, center.y), radius_m, fill=False,
                            edgecolor=SITE_COLOR, linestyle='--', linewidth=1.4,
                            zorder=9, label=f'검토 반경 {radius_m:,}m'))


def _draw_scalebar(ax, extent_m: float):
    """축척 막대 — 미터 단위 좌표계라 그대로 그릴 수 있다."""
    for unit in (5000, 2000, 1000, 500, 200, 100):
        if unit <= extent_m * 0.6:
            bar = unit
            break
    else:
        bar = int(extent_m * 0.4)

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    sx = x0 + (x1 - x0) * 0.06
    sy = y0 + (y1 - y0) * 0.06
    ax.plot([sx, sx + bar], [sy, sy], color='#2b2f36', linewidth=3, zorder=12)
    label = f'{bar / 1000:g}km' if bar >= 1000 else f'{bar}m'
    ax.text(sx + bar / 2, sy + (y1 - y0) * 0.012, label,
            ha='center', va='bottom', fontsize=9, color='#2b2f36', zorder=12)


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


def _finish(fig, legend: bool = True) -> bytes:
    import matplotlib.pyplot as plt

    ax = fig.axes[0]
    if legend:
        handles, labels = ax.get_legend_handles_labels()
        seen: dict[str, object] = {}
        for h, l in zip(handles, labels):
            seen.setdefault(l, h)
        if seen:
            ax.legend(seen.values(), seen.keys(), loc='upper right',
                      fontsize=8, framealpha=0.9)
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

    for f in features:
        c = JIMOK_COLORS.get(f['jimok'], DEFAULT_JIMOK_COLOR)
        _plot_geom(ax, f['geom'], color=c, alpha=0.65, zorder=3,
                   label=f['jimok'], linewidth=0.3)

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

    for lyr in layers:
        color = REGULATION_COLOR if lyr.get('overlapping') else PROXIMITY_COLOR
        for g in lyr['geoms']:
            _plot_geom(ax, g, color=color, alpha=0.35, zorder=3,
                       label=lyr['name'], linewidth=0.6)

    _draw_site(ax, center, radius_m)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def surroundings_map(lat: float, lng: float, radius_m: int,
                     buildings: list, roads: list) -> bytes:
    """주변 현황도 — 건물·도로 (이격거리 판단의 시각적 근거)"""
    center = geo.point_metric(lat, lng)
    extent = max(radius_m * 2.5, 1200)
    fig, ax = _new_axes('주변 현황도 (건물·도로)', extent, center)

    for g in roads:
        _plot_geom(ax, g, color='#8a9099', linewidth=1.0, zorder=2, label='도로')
    for g in buildings:
        _plot_geom(ax, g, color='#5a6270', alpha=0.8, zorder=3,
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
                            edgecolor=c, linewidth=1.6, zorder=4,
                            label=f'{r["target"]} {r["distance_m"]:,}m'))

    for f in facilities:
        p = geo.point_metric(f['lat'], f['lng'])
        inside = any(f['distance_m'] <= r['distance_m'] for r in rings)
        ax.plot([p.x], [p.y], marker='o', markersize=6, zorder=6,
                color='#c0392b' if inside else '#4a4f57',
                label='기준 내 정온시설' if inside else '정온시설')

    _draw_site(ax, center)
    _draw_scalebar(ax, extent)
    return _finish(fig)
