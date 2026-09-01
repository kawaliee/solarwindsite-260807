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
#: 사업구역 경계선 색 — 항목별 지도에서 붉은 점선으로 낸다.
SITE_BOUNDARY_COLOR = '#d0021b'

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


#: 지도 캔버스 규격(가로·세로 인치). **종횡비를 여기서만 정한다.**
#:
#: 종전에는 모든 지도가 8×8 정사각이었고, 보고서는 폭만 맞춘 뒤 높이를
#: 원본 비율로 계산했다. 그래서 화면 캡처(브라우저 뷰포트, 가로로 긴
#: 1.9:1)와 서버 렌더(정사각)가 한 면에 섮이면 높이가 두 배까지
#: 벌어졌다 — 농업진흥지역도 1.70in vs 생태자연도 3.38in(실측).
#:
#: 캔버스를 고정하고 **지도 범위(bbox)를 캔버스에 맞춰 넓혀** 채운다.
#: 짧은 쪽은 요청받은 extent를 그대로 쓰고 긴 쪽만 늘리므로, 보여야 할
#: 것이 잘리지 않고 그림도 늘어나거나 찌그러지지 않는다.
PANEL_CANVAS = (8.0, 8.0)      # 패널 — ③ 환경성 같은 반폭 지도
# 전폭 — 제약도·용도지역도·국가유산도·계통도.
# 10 : 5.25 = 1.905 는 화면 캡처의 실측 종횡비(1.907)에 맞춘 값이다. 전폭
# 제약도는 화면과 그대로 맞아야 해서 캡처를 계속 쓰므로, 서버 렌더가 같은
# 비율이어야 캡처든 재렌더든 한 문서 안에서 같은 높이로 실린다.
FULL_CANVAS = (10.0, 5.25)

#: 제목을 올릴 띠. 축은 나머지를 다 차지한다.
TITLE_TOP = 0.945
CANVAS_DPI = 140


def _canvas_extent(extent_m: float, canvas) -> tuple[float, float]:
    """캔버스 종횡비에 맞춘 (x 반지름, y 반지름). 짧은 쪽이 extent_m이다."""
    w, h = canvas[0], canvas[1] * TITLE_TOP
    ar = w / h
    return (extent_m * ar, extent_m) if ar >= 1 else (extent_m, extent_m / ar)


def _new_axes(title: str, extent_m: float, center, basemap: str = STYLE_SATELLITE,
              canvas=PANEL_CANVAS):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    _configure_font()
    fig, ax = plt.subplots(figsize=canvas, dpi=CANVAS_DPI)
    # 축이 그림을 가득 채우게 한다 — 그래야 저장된 PNG의 픽셀 크기가
    # figsize × dpi로 **항상 같아진다.** 좌우 흰 여백도 생기지 않는다.
    fig.subplots_adjust(left=0, right=1, bottom=0, top=TITLE_TOP)
    ax.set_title(title, fontsize=13, pad=10)

    ex, ey = _canvas_extent(extent_m, canvas)
    # 배경지도는 넓은 쪽을 덮을 만큼 받아야 빈 자리가 안 생긴다.
    bg = _basemap(center, max(ex, ey), basemap) if basemap else None
    if bg is not None:
        img, ext = bg
        # origin='upper' — 이미지 첫 행이 북쪽이다. extent가 5179 미터라 축과 1:1로 맞는다.
        ax.imshow(img, extent=ext, origin='upper', zorder=0,
                  interpolation='bilinear')
        ax._ws_has_basemap = True
    else:
        ax._ws_has_basemap = False

    ax.set_xlim(center.x - ex, center.x + ex)
    ax.set_ylim(center.y - ey, center.y + ey)
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
    """
    폴리곤 경계선만 그린다 — 배경지도가 비치도록 채움 위에 얹는다.

    `label`은 **첫 조각에만** 붙인다. 조각마다 붙이면 흩어진 이격 범위
    하나하나가 범례에 따로 실려 범례가 지도를 덮는다.
    """
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

    if isinstance(g, (MultiPolygon, GeometryCollection)):
        for i, part in enumerate(g.geoms):
            _outline(ax, part, **(kw if i == 0 else {**kw, 'label': None}))
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
    # tight_layout이나 bbox_inches='tight'를 쓰지 않는다.
    #
    # 둘 다 그린 내용에 따라 출력 크기를 바꾸므로, 같은 규격으로
    # 주문해도 장마다 픽셀 크기가 달라진다. 축이 그림을 가득 채우도록
    # `_new_axes`에서 이미 잡아 두었으므로 그대로 저장하면
    # **figsize × dpi 크기가 그대로 나온다.**
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


def landslide_map(area, png: bytes, extent: tuple,
                  turbines: list | None = None) -> bytes:
    """
    산사태위험지도 — 산림청 WMS 래스터를 사업구역 위에 얹는다.

    이 어댑터는 등급을 숫자로 주지 않아 **이미지를 받아 픽셀 색을 등급으로
    역변환**해 판정한다(providers/landslide.py). 그 이미지를 그대로 지도로
    쓰면 판정과 그림이 같은 원본에서 나오므로 어긋날 수가 없다.

    extent = (minx, maxx, miny, maxy) EPSG:5179 — WMS bbox와 같은 값이다.
    """
    import io as _io

    from PIL import Image

    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    ex = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes('산사태위험등급', ex, center)

    try:
        img = Image.open(_io.BytesIO(png)).convert('RGBA')
        ax.imshow(img, extent=extent, origin='upper', zorder=3,
                  alpha=0.55 if _has_bg(ax) else 0.85, interpolation='nearest')
    except Exception:                                           # noqa: BLE001
        logger.exception('산사태위험지도 이미지 렌더 실패')

    _outline(ax, area, color=SITE_BOUNDARY_COLOR, linewidth=1.8,
             linestyle=(0, (5, 3)), zorder=6, label='사업구역 경계')

    # 발전기 위치를 함께 찍는다 — 어느 호기가 위험등급 위에 서는지가
    # 곧 배치 조정의 대상이다.
    for i, p in enumerate(turbines or [], 1):
        ax.plot([p.x], [p.y], marker='o', markersize=6, color='#111417',
                markeredgecolor='white', markeredgewidth=1.2, zorder=8)
        ax.annotate(str(i), (p.x, p.y), textcoords='offset points',
                    xytext=(0, 8), ha='center', fontsize=7.5,
                    color='#111417', zorder=9, path_effects=_halo(2.5))

    # 범례는 색 표본으로 직접 만든다 — 래스터라 plot 라벨이 생기지 않는다.
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    from .providers.landslide import GRADE_COLOR, GRADE_LABEL
    handles = [Patch(facecolor=GRADE_COLOR[g], edgecolor='none',
                     label=GRADE_LABEL[g]) for g in sorted(GRADE_COLOR)]
    handles.append(Line2D([0], [0], color=SITE_BOUNDARY_COLOR, lw=1.8,
                          linestyle=(0, (5, 3)), label='사업구역 경계'))
    ax.legend(handles=handles, loc='upper right', fontsize=7.5,
              framealpha=0.9, borderpad=0.6)

    _draw_scalebar(ax, ex)
    return _finish(fig, legend=False)


def item_map(area, geoms: list, title: str, color: str) -> bytes:
    """
    항목별 환경성 평가 지도 — 사업구역 경계 위에 **그 항목 하나**만 얹는다.

    종합 제약도는 배제·조건부·제약없음을 한 번에 보여주지만, "농업진흥지역이
    정확히 어디인가"·"철새도래지가 사업구역과 얼마나 겹치는가"처럼 항목
    하나를 짚어 협의할 때는 다른 레이어가 섞이면 오히려 방해가 된다.
    인허가 실무 협의는 항목마다 담당 기관이 달라 한 장에 하나씩 낸다.
    """
    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    extent = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes(title, extent, center)

    # 사업구역은 **붉은 점선**으로 낸다. 규제 구역은 색면으로 칠해지므로
    # 경계까지 실선이면 둘이 같은 종류의 자료로 읽힌다. 인허가 협의 도면은
    # 사업자가 그은 선(사업구역)과 기관이 준 자료(규제 구역)를 선종으로
    # 가르는 것이 관행이다.
    _outline(ax, area, color=SITE_BOUNDARY_COLOR, linewidth=1.8,
             linestyle=(0, (5, 3)), zorder=6, label='사업구역 경계')

    hit = False
    for g in (geoms or []):
        if g is None or g.is_empty:
            continue
        hit = True
        _plot_geom(ax, g, color=color, alpha=0.5 if _has_bg(ax) else 0.65,
                   zorder=3, label=title)
        _outline(ax, g, color=color, linewidth=1.0, zorder=4)

    if not hit:
        ax.text(0.5, 0.5, '사업구역 내 해당 없음', transform=ax.transAxes,
                ha='center', va='center', fontsize=11, color='#333333',
                zorder=13, path_effects=_halo(3))

    _draw_scalebar(ax, extent)
    return _finish(fig)


#: 용도지역 4분류 색 — 국토계획법 순서(도시·관리·농림·자연환경보전).
#: 다른 분류(세부 지구 등)는 회색으로 낸다 — 색을 지어내지 않는다.
ZONING_COLORS = {
    '도시지역': '#4a6fa5', '관리지역': '#c9a227',
    '농림지역': '#4caf50', '자연환경보전지역': '#2e7d32',
}
_ZONING_DEFAULT = '#8a8f98'


def zoning_map(area, zones: dict) -> bytes:
    """
    용도지역 구성도 — 국토계획법 4종 분류를 사업구역 위에 색으로 낸다.
    zones: {용도지역명: shapely 도형}
    """
    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    extent = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes('용도지역 구성', extent, center, canvas=FULL_CANVAS)

    _outline(ax, area, color='#1B4F8C', linewidth=1.6, zorder=5, label='사업구역 경계')
    for name, g in zones.items():
        if g is None or g.is_empty:
            continue
        c = ZONING_COLORS.get(name, _ZONING_DEFAULT)
        _plot_geom(ax, g, color=c, alpha=0.5 if _has_bg(ax) else 0.65,
                   zorder=3, label=name)
        _outline(ax, g, color=c, linewidth=0.8, zorder=4)

    _draw_scalebar(ax, extent)
    return _finish(fig)


def constraint_map(area, blocked, conditional, free,
                   turbines: list | None = None,
                   ordinance_house=None, ordinance_road=None,
                   ordinance_road_uncertain=None,
                   grandfathered: bool = False,
                   road_detail: list | None = None) -> bytes:
    """
    사업구역 제약도 — 배제·조건부·제약없음을 색으로 나눠 위성영상 위에 얹는다.

    도형은 모두 EPSG:5179 shapely 객체다. 제약이 강한 쪽을 위에 쌓아,
    겹치는 지점에서 더 엄한 판정이 보이게 한다.

    조례 이격 범위를 주면 파선 윤곽으로 덧그린다. 면은 이미 배제·조건부에
    들어 있으므로 다시 칠하지 않는다 — 붉은 면만 보면 규제 레이어 때문인지
    조례 이격 때문인지 알 수 없는데, 이 셋은 다음 행동이 전혀 다르다.

        주거 이격      부지를 옮기거나 이격을 확보한다
        국도·지방도    부지를 옮긴다 (거리를 바꿀 수 없다)
        시·군도        **지자체에 군도 노선인지 확인**한다
    """
    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    # 구역이 화면에 꽉 차지 않도록 15% 여백을 둔다
    extent = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes('사업구역 제약도', extent, center, canvas=FULL_CANVAS)

    for g, color, label in ((free, '#2e9e2e', '제약 없음'),
                            (conditional, '#e8a33d', '조건부'),
                            (blocked, '#d9363e', '배제')):
        if g is None or g.is_empty:
            continue
        _plot_geom(ax, g, color=color, alpha=0.45 if _has_bg(ax) else 0.65,
                   zorder=3, label=label)
        _outline(ax, g, color=color, linewidth=0.8, zorder=4)

    _ordinance_and_frame(ax, area, extent, turbines, ordinance_house,
                        ordinance_road, ordinance_road_uncertain,
                        grandfathered, road_detail)
    return _finish(fig)


def _ordinance_and_frame(ax, area, extent, turbines, ordinance_house,
                         ordinance_road, ordinance_road_uncertain,
                         grandfathered, road_detail) -> None:
    """
    조례 이격 윤곽(파선) · 도로 라벨 · 사업구역 검은 윤곽선 · 호기 마커 ·
    축척 막대 — `constraint_map`과 `parcel_constraint_map`이 공유하는
    "틀" 부분. 채색 방식(연속 면 vs 필지별)만 다르고 이 틀은 같아야
    두 지도가 같은 지도로 읽힌다.

    조례 이격은 면을 다시 칠하지 않고 **윤곽만** 덧그린다. 붉은 면만 보면
    규제 레이어 때문인지 조례 이격 때문인지 알 수 없는데, 이 둘은 다음
    행동이 다르다(부지 변경 / 이격 확보 / 지자체 확인).
    경과규정 대상이면 '사업 불가'가 아니다 — 부칙으로 종전 기준이 적용될 수
    있어 조건부로 집계했다. 범례가 표와 어긋나면 지도만 보고 접게 된다.
    """
    verdict = '경과규정 검토 대상' if grandfathered else '사업 불가'
    # 색은 화면 지도(SitePicker)와 같은 체계다 — 배제 도로는 시안,
    # 조건부 도로는 자홍. 문서와 화면이 다른 색을 쓰면 대조가 안 된다.
    for g, color, style, label in (
            (ordinance_house, '#7b1fa2', '--', f'조례 주거 이격 ({verdict})'),
            (ordinance_road, '#0097a7', '--',
             f'조례 도로 이격 — 국도·지방도 ({verdict})'),
            (ordinance_road_uncertain, '#c2185b', ':',
             '조례 도로 이격 — 시·군도 (군도 여부 확인 필요)')):
        if g is None or g.is_empty:
            continue
        _outline(ax, g, color=color, linewidth=1.6, zorder=5,
                 linestyle=style, label=label)

    _road_labels(ax, road_detail)

    _outline(ax, area, color='#111111', linewidth=2.0, zorder=6)

    for i, p in enumerate(turbines or [], start=1):
        ax.plot(p.x, p.y, marker='o', markersize=7, color='#ffffff',
                markeredgecolor='#111111', markeredgewidth=1.2, zorder=7)
        ax.annotate(str(i), (p.x, p.y), fontsize=8, fontweight='bold',
                    ha='center', va='center', zorder=8,
                    path_effects=_halo(2) if _has_bg(ax) else None)

    _draw_scalebar(ax, extent)


#: 필지 채색 색 — 화면(SitePicker.tsx의 SCREEN_STYLE)과 **완전히 같은 값**을
#: 쓴다. 색이 다르면 화면과 보고서가 또 어긋나 보인다.
SCREEN_COLORS = {
    'POSSIBLE': '#52c41a', 'CONDITIONAL': '#faad14',
    'IMPOSSIBLE': '#ff4d4f', 'UNKNOWN': '#40a9ff',
}
SCREEN_LABEL = {
    'POSSIBLE': '가능', 'CONDITIONAL': '조건부',
    'IMPOSSIBLE': '배제', 'UNKNOWN': '미확인',
}


def parcel_constraint_map(area, parcels: list[dict], turbines=None,
                          ordinance_house=None, ordinance_road=None,
                          ordinance_road_uncertain=None,
                          grandfathered: bool = False,
                          road_detail: list | None = None) -> bytes:
    """
    사업구역 제약도 — **화면(SitePicker)과 같은 필지 도형·같은 색**으로 그린다.

    농업진흥지역도 같은 구역 단위 규제 레이어는 정부 원본 데이터가 실제
    필지 경계보다 성기게 단순화돼 있다. 그 도형을 연속 면으로 그리면,
    화면에서 필지 단위로 맞춰 칠한 결과와 좁은 물길·경계 부근에서 미세하게
    어긋나 보인다("경계가 강을 넘어간다"). 태양광은 사업 단위가 필지이므로
    보고서도 화면과 **똑같은 필지 도형**을 그 등급 색으로 칠해, 화면에서 본
    구역이 문서에서도 그대로 나오게 한다.

    parcels: screening.screen_area()가 낸 [{grade, rings}, …].
    rings는 WGS84 [[lat,lng], …] — 화면에 보내는 것과 같은 값이라
    여기서 새로 조회하지 않고 그대로 쓴다.
    """
    from shapely.geometry import Polygon

    center = area.centroid
    minx, miny, maxx, maxy = area.bounds
    extent = max(maxx - minx, maxy - miny) / 2 * 1.15
    fig, ax = _new_axes('사업구역 제약도 (필지별)', extent, center, canvas=FULL_CANVAS)

    order = {'NOT_APPLICABLE': 0, 'POSSIBLE': 1, 'CONDITIONAL': 2,
             'UNKNOWN': 3, 'IMPOSSIBLE': 4}
    rows = sorted(parcels, key=lambda p: order.get(p.get('grade'), 0))
    seen_label = set()
    for p in rows:
        grade = p.get('grade')
        if grade == 'NOT_APPLICABLE' or grade not in SCREEN_COLORS:
            continue
        color = SCREEN_COLORS[grade]
        label = None if grade in seen_label else SCREEN_LABEL.get(grade, grade)
        seen_label.add(grade)
        for ring in p.get('rings') or []:
            if len(ring) < 4:
                continue
            mpts = [geo.point_metric(lat, lng) for lat, lng in ring]
            g = Polygon([(pt.x, pt.y) for pt in mpts])
            if not g.is_valid or g.is_empty:
                continue
            # 사업구역 경계에 걸친 필지는 구역 안쪽 부분만 칠한다. 전체를
            # 칠하면 필지가 검은 경계선 밖으로 삐져나와 보인다 — 화면은
            # 필지를 통째로 눌러 담지만(후보 판단은 그래도 되지만), 지도에
            # 그릴 때는 실제 사업구역만 색칠해야 "경계 밖으로 넘어간다"는
            # 오해가 생기지 않는다.
            g = g.intersection(area)
            if g.is_empty:
                continue
            _plot_geom(ax, g, color=color, alpha=0.55 if _has_bg(ax) else 0.7,
                       zorder=3, label=label)
            label = None                       # 링이 여러 개면 첫 링에만 범례
            _outline(ax, g, color=color, linewidth=0.35, zorder=4)

    _ordinance_and_frame(ax, area, extent, turbines, ordinance_house,
                        ordinance_road, ordinance_road_uncertain,
                        grandfathered, road_detail)
    return _finish(fig)


#: 지도에 이름을 적을 도로 수. 다 적으면 글자가 겹쳐 아무것도 안 읽힌다.
#: 침범 면적이 큰 것부터 — 사업에 실제로 걸리는 도로가 먼저다.
ROAD_LABEL_MAX = 4


def _road_labels(ax, road_detail: list | None) -> None:
    """
    도로 선과 **이름·이격거리**를 지도에 직접 적는다.

    범례만으로는 "어느 도로가 어디를 잘랐는가"를 알 수 없다. 인허가 실무
    검토서가 지도 위에 「도로 이격(819번 지방도) 500m」를 화살표와 함께
    적어 두는 까닭이다 — **지도 한 장만 떼어 봐도 읽혀야** 회의에서 쓴다.
    """
    if not road_detail:
        return
    from shapely.geometry import LineString, MultiLineString

    for d in (road_detail or [])[:ROAD_LABEL_MAX]:
        color = '#0097a7' if d.get('blocked') else '#c2185b'
        parts = []
        for ln in d.get('line') or []:
            if len(ln) >= 2:
                pts = [geo.point_metric(lat, lng) for lat, lng in ln]
                parts.append(LineString([(q.x, q.y) for q in pts]))
        if not parts:
            continue
        merged = parts[0] if len(parts) == 1 else MultiLineString(parts)
        for g in getattr(merged, 'geoms', [merged]):
            _plot_geom(ax, g, color=color, linewidth=2.4, zorder=5,
                       solid_capstyle='round')
        # 가장 긴 조각의 가운데에 이름을 얹는다 — 짧은 조각에 적으면
        # 화면 끝에 붙어 잘린다.
        longest = max(parts, key=lambda g: g.length)
        pt = longest.interpolate(0.5, normalized=True)
        ax.annotate(f'{d["name"]}\n이격 {d["distance_m"]:,}m',
                    (pt.x, pt.y), fontsize=8.5, fontweight='bold',
                    color=color, ha='center', va='center', zorder=11,
                    path_effects=_halo(3.5),
                    bbox=dict(boxstyle='round,pad=0.28', facecolor='white',
                              edgecolor=color, linewidth=1.0, alpha=0.88))


#: 계통 경로도에 그릴 변전소 수. 최근접 몇 개만 — 다 그리면 선이 뒤엉킨다.
GRID_ROUTE_MAX = 3
#: 이 전압 이상만 연계 대상으로 본다(한전 154kV 이상 계통).
GRID_MIN_VOLT = 154_000


def grid_route_map(area, substations: list, road_m: float | None = None) -> bytes:
    """
    전력계통 연계 경로도 — 사업구역에서 최근접 변전소까지.

    "22km 떨어져 있다"는 문장만으로는 선로를 어느 방향으로 끌어야 하는지,
    그 사이에 무엇이 있는지 알 수 없다. 방향과 거리를 지도에 그려야 선로
    경과지 협의를 시작할 수 있다.

    substations: providers/osm.py가 낸 raw['substations'] —
      {name, voltage, lat, lng, distance_m, margin_substation_kw, margin_line_kw}
    좌표는 WGS84라 여기서 미터 좌표로 옮긴다.

    ⚠️ 직선거리다. 실제 선로는 도로·지형을 따라가므로 더 길다 — 그 사실을
       지도 안에 적어 둔다(카드 요약이 도로망 경로 거리를 따로 인용한다).
    """
    picks = [s for s in (substations or [])
             if s.get('lat') and s.get('lng')
             and (s.get('voltage') or 0) >= GRID_MIN_VOLT][:GRID_ROUTE_MAX]
    center = area.centroid
    if not picks:
        # 연계 대상이 없으면 구역만 그려 둔다 — 빈 그림을 내는 편이
        # '지도를 못 만들었다'는 오해보다 낫다.
        minx, miny, maxx, maxy = area.bounds
        extent = max(maxx - minx, maxy - miny) / 2 * 1.3
        fig, ax = _new_axes('전력계통 연계 경로', extent, center, canvas=FULL_CANVAS)
        _outline(ax, area, color='#1B4F8C', linewidth=2.0, zorder=5,
                 label='사업구역')
        ax.text(0.5, 0.5, '반경 30km 내 154kV 이상 변전소 없음',
                transform=ax.transAxes, ha='center', va='center',
                fontsize=11, color='#333333', zorder=13, path_effects=_halo(3))
        _draw_scalebar(ax, extent)
        return _finish(fig)

    pts = [geo.point_metric(s['lat'], s['lng']) for s in picks]
    # 구역과 변전소가 **모두** 들어오도록 화면을 잡는다. 구역만 기준으로 잡으면
    # 20km 밖 변전소가 화면 밖으로 나가 경로가 안 보인다.
    xs = [center.x] + [p.x for p in pts]
    ys = [center.y] + [p.y for p in pts]
    extent = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * 1.25 or 1000
    mid = type(center)((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
    title = '전력계통 연계 경로'
    if road_m:
        title += ' — 도로망 %.1fkm / 직선 %.1fkm' % (
            road_m / 1000, (picks[0].get('distance_m') or 0) / 1000)
    fig, ax = _new_axes(title, extent, mid, canvas=FULL_CANVAS)

    _plot_geom(ax, area, color='#1B4F8C', alpha=0.5, zorder=4, label='사업구역')
    _outline(ax, area, color='#1B4F8C', linewidth=1.8, zorder=5)

    for i, (s, p) in enumerate(zip(picks, pts)):
        # 최근접 한 곳만 굵은 실선, 나머지는 옅은 파선 — 무엇이 1순위인지
        # 색이 아니라 선 굵기로 먼저 읽히게 한다.
        first = i == 0
        ax.plot([center.x, p.x], [center.y, p.y],
                color='#C0392B' if first else '#8A8F98',
                linewidth=2.6 if first else 1.4,
                linestyle='-' if first else '--',
                zorder=6, path_effects=_halo(3.5),
                label='최근접 연계점' if first else None)
        ax.plot([p.x], [p.y], marker='s', markersize=11 if first else 8,
                color='#C0392B' if first else '#5A6270',
                markeredgecolor='white', markeredgewidth=1.2, zorder=7)
        ax.annotate(_grid_label(s), (p.x, p.y),
                    textcoords='offset points', xytext=(0, 14),
                    ha='center', fontsize=8.5, fontweight='bold',
                    color='#111417', zorder=8, path_effects=_halo(3),
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                              edgecolor='#C0392B' if first else '#8A8F98',
                              linewidth=1.0, alpha=0.9))

    ax.plot([center.x], [center.y], marker='*', markersize=18, color='#1f77d0',
            markeredgecolor='white', markeredgewidth=1.2, zorder=9)
    _draw_scalebar(ax, extent)
    return _finish(fig)


def _grid_label(s: dict) -> str:
    """변전소 이름표 — 이름·전압·거리, 여유용량이 있으면 한 줄 더."""
    kv = int((s.get('voltage') or 0) / 1000)
    out = '%s (%dkV)\n%.1fkm' % (s.get('name') or '명칭 미상', kv,
                                 (s.get('distance_m') or 0) / 1000)
    margin = s.get('margin_line_kw')
    if margin is not None:
        out += '\n선로여유 %s kW' % f'{int(margin):,}'
    return out


# ----------------------------------------------------------------------
# 문화재 · 국가유산
# ----------------------------------------------------------------------
#: 지도에 그릴 국가유산 수. 다 그리면 이름표가 서로를 덮는다.
HERITAGE_MAX = 6

#: 국가유산 마커 색 — 안전/문화재 계열 색과 맞춘다.
HERITAGE_COLOR = '#7C5BB5'
#: 조사구역(면)은 마커와 갈라 보이도록 다른 색을 준다.
SURVEY_COLOR = '#B5426E'


def heritage_map(area, heritages: list, survey_geoms: list | None = None) -> bytes:
    """
    문화재·국가유산 위치도 — 계통 경로도와 같은 방식으로 낸다.

    "1.77km 지점입니다"라는 문장만으로는 어느 방향에 무엇이 있는지 알 수 없어
    현상변경 협의를 시작할 수 없다. 계통도가 변전소를 점으로 찍고 거리를
    붙이듯, 국가유산도 위치와 거리를 지도에 올려야 협의 상대가 정해진다.

    heritages: providers/local_spatial.py가 낸 raw['heritages'] —
      {name, kind, distance_m, lat, lng}
    survey_geoms: 사업구역에 걸치는 국가유산조사구역 도형(EPSG:5179).
      조사구역은 점이 아니라 면이라, 걸치는 범위를 그대로 보여야
      매장유산 지표조사 대상 여부를 눈으로 가릴 수 있다.
    """
    picks = [h for h in (heritages or []) if h.get('lat') and h.get('lng')]
    picks.sort(key=lambda h: h.get('distance_m') or 0)
    picks = picks[:HERITAGE_MAX]

    center = area.centroid
    pts = [geo.point_metric(h['lat'], h['lng']) for h in picks]
    # 구역과 유산이 **모두** 들어오도록 잡는다 — 구역만 기준이면 2km 밖
    # 유산이 화면 밖으로 나가 거리 라벨이 뜻을 잃는다.
    minx, miny, maxx, maxy = area.bounds
    xs = [minx, maxx] + [p.x for p in pts]
    ys = [miny, maxy] + [p.y for p in pts]
    extent = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * 1.2 or 1000
    mid = type(center)((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)

    fig, ax = _new_axes('문화재 · 국가유산 위치도', extent, mid,
                        canvas=FULL_CANVAS)

    # 사업구역은 다른 항목별 지도와 같은 붉은 점선으로 낸다.
    _outline(ax, area, color=SITE_BOUNDARY_COLOR, linewidth=1.8,
             linestyle=(0, (5, 3)), zorder=6, label='사업구역 경계')

    for g in (survey_geoms or []):
        if g is None or g.is_empty:
            continue
        _plot_geom(ax, g, color=SURVEY_COLOR,
                   alpha=0.45 if _has_bg(ax) else 0.6, zorder=3,
                   label='국가유산조사구역')
        _outline(ax, g, color=SURVEY_COLOR, linewidth=1.0, zorder=4)

    for i, (h, p) in enumerate(zip(picks, pts)):
        first = i == 0
        # 최근접 한 곳만 구역과 잇는 선을 그어 거리를 눈으로 보이게 한다.
        #
        # ⚠️ 지도 중심(mid)이 아니라 **사업구역 경계에서 그 유산에 가장
        # 가까운 점**에서 긋는다. "975m"라는 거리 자체가 경계 기준으로
        # 잰 값인데(구역 중심 기준이 아니다 — 3차 실측 이후 boundary 기준),
        # 선을 mid에서 그으면 구역 안쪽까지 파고들어 화면이 그 거리의 뜻과
        # 다르게 읽힌다(실측 지적: 회령진성 975m 선이 구역 중앙까지 뚫고
        # 들어가 있었음).
        if first:
            from shapely.ops import nearest_points
            edge = nearest_points(area.boundary, p)[0]
            ax.plot([edge.x, p.x], [edge.y, p.y], color=HERITAGE_COLOR,
                    linewidth=1.8, linestyle='--', zorder=5,
                    path_effects=_halo(3.0), label='최근접 국가유산')
        ax.plot([p.x], [p.y], marker='^', markersize=13 if first else 9,
                color=HERITAGE_COLOR if first else '#9B87C4',
                markeredgecolor='white', markeredgewidth=1.2, zorder=7)
        ax.annotate(_heritage_label(h), (p.x, p.y),
                    textcoords='offset points', xytext=(0, 15),
                    ha='center', fontsize=8.5,
                    fontweight='bold' if first else 'normal',
                    color='#111417', zorder=8, path_effects=_halo(3),
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                              edgecolor=HERITAGE_COLOR if first else '#9B87C4',
                              linewidth=1.0, alpha=0.9))

    if not picks and not (survey_geoms or []):
        ax.text(0.5, 0.5, '조회 범위 내 지정·등록 국가유산 없음',
                transform=ax.transAxes, ha='center', va='center',
                fontsize=11, color='#333333', zorder=13, path_effects=_halo(3))

    _draw_scalebar(ax, extent)
    return _finish(fig)


def _heritage_label(h: dict) -> str:
    """유산 이름표 — 명칭 · 종별 · 사업구역으로부터의 직선거리."""
    name = (h.get('name') or '명칭 미상').strip()
    kind = (h.get('kind') or '').strip()
    d = h.get('distance_m')
    out = name if not kind else '%s (%s)' % (name, kind)
    if d is not None:
        out += '\n' + ('구역 내' if d <= 0 else geo.format_distance(d))
    return out
