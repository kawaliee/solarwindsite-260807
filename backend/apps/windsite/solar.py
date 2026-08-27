"""
일사량 — 태양광 사업성 판정의 자원 축
---------------------------------------------------------------
풍력에서 풍황이 하는 자리를 태양광에서는 일사량이 맡는다. 다만 자료 하나로
단정하지 않는다. 일사량은 발전량과 수익을 직접 가르는 수치라, 한 출처가
치우쳐 있으면 그 오차가 사업 판단에 그대로 실린다.

■ 세 갈래로 받아 나란히 낸다

  ① 기상청 GK2A DSR   2km 격자 · 5개년 — **공간 분해능이 가장 좋다**
  ② Global Solar Atlas 250m 장기평균 — 절대값 기준(World Bank/ESMAP)
  ③ 기상청 ASOS 일사   관측소 실측 — 모델값이 아닌 유일한 값

■ ⚠️ GK2A DSR을 그대로 연환산하면 안 된다

파일의 DSR은 W/m²인데 **24시간 평균이 아니다**. 중앙값 261.5를 8,760시간에
곱하면 2,291 kWh/m²/yr이 나오는데, 국내 실제는 1,500 안팎이다. 관측이
이루어지는 주간대의 평균으로 보인다.

전국 8개 지점에서 Global Solar Atlas와 대조한 결과 비율이 **0.589,
변동계수 3.4%** 로 전국에서 거의 일정했다(2026-08 실측). 그래서 환산이
성립한다고 보고 이 계수를 쓴다.

    서울 0.590 · 홍성 0.558 · 삼척 0.633 · 목포 0.587
    대구 0.594 · 제주 0.577 · 강릉 0.596 · 광주 0.576

**다만 이 값은 기상청이 고시한 계수가 아니라 경험적으로 맞춘 값이다.**
그래서 환산값을 낼 때는 그 사실을 함께 밝히고, 원값(W/m²)과 대조에 쓴
Global Solar Atlas 값을 함께 싣는다. 공간 비교(어디가 더 좋은가)는 계수와
무관하게 원값만으로 성립하므로, 그쪽이 더 믿을 만한 쓰임이다.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import threading

from django.conf import settings

from . import httpcache

logger = logging.getLogger(__name__)

SOLAR_DIR = os.path.join(settings.BASE_DIR, 'data', 'solar')
KMA_DIR = os.path.join(SOLAR_DIR, 'kma_resource_map')

#: GK2A DSR(W/m²) → 연간 수평면 일사량(kWh/m²/yr) 보정 계수.
#: Global Solar Atlas와 전국 8지점 대조로 얻은 값(변동계수 3.4%).
#: 고시값이 아니므로 이 계수로 낸 수치는 항상 '보정값'으로 표기한다.
DSR_TO_GHI = 0.589

#: 보정 계수의 근거 — 화면·보고서에 그대로 인용한다.
DSR_CALIBRATION_NOTE = (
    'Global Solar Atlas 전국 8지점 대조로 얻은 보정계수 0.589를 적용한 값입니다 '
    '(변동계수 3.4%). 기상청이 고시한 환산식이 아니므로 절대값은 참고로 보고, '
    '지역 간 비교에는 원값(W/m²)을 쓰십시오.'
)

#: GK2A 한반도 영역 격자 (파일 속성에서 읽은 값 — 파일마다 같다)
_LCC_PROJ = ('+proj=lcc +lat_1=30 +lat_2=60 +lat_0=38 +lon_0=126 '
             '+x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs')
_UL_X, _UL_Y, _PIXEL = -899000.0, 899000.0, 2000.0
_GRID = 900

#: Global Solar Atlas 지점 조회 — 인증키가 필요 없다(공개 엔드포인트).
GSA_URL = 'https://api.globalsolaratlas.info/data/lta'

#: ASOS 일별 자료의 일사량 항목(합계 일사량, MJ/m²)
ASOS_GSR_KEY = 'sumGsr'
#: MJ/m² → kWh/m²
MJ_TO_KWH = 1 / 3.6

_cache: dict | None = None
_lock = threading.Lock()


class SolarUnavailable(RuntimeError):
    """일사량 자료가 없거나 읽지 못했다. '일사량이 낮다'와 구분한다."""


# ======================================================================
# ① 기상청 GK2A DSR (2km 격자)
# ======================================================================
def _load_kma() -> dict:
    """연도별 DSR 배열을 한 번만 읽어 둔다. {'years': {2020: arr, …}}"""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        years: dict[int, object] = {}
        try:
            import netCDF4
            import numpy as np
        except ImportError:                                     # pragma: no cover
            logger.warning('netCDF4 미설치 — 기상청 일사량 자료를 읽을 수 없습니다.')
            _cache = {'years': {}}
            return _cache

        for p in sorted(glob.glob(os.path.join(KMA_DIR, '**', '*.nc'), recursive=True)):
            m = re.search(r'(\d{4})\.nc$', os.path.basename(p))
            if not m:
                continue
            try:
                with netCDF4.Dataset(p) as d:
                    a = d.variables['DSR'][:]
                years[int(m.group(1))] = np.ma.filled(a.astype('float64'), np.nan)
            except Exception:                                   # noqa: BLE001
                logger.warning('일사량 파일 판독 실패 %s', p, exc_info=True)
        _cache = {'years': years}
        logger.info('기상청 일사량 %d개 연도 적재', len(years))
        return _cache


def _rowcol(lat: float, lng: float) -> tuple[int, int] | None:
    from pyproj import CRS, Transformer
    t = Transformer.from_crs('EPSG:4326', CRS.from_proj4(_LCC_PROJ), always_xy=True)
    x, y = t.transform(lng, lat)
    c = int((x - _UL_X) / _PIXEL)
    r = int((_UL_Y - y) / _PIXEL)
    if not (0 <= r < _GRID and 0 <= c < _GRID):
        return None
    return r, c


def kma_dsr(lat: float, lng: float) -> dict:
    """
    기상청 GK2A DSR — 연도별 값과 평균.

    격자가 2km라 필지 하나는 한 칸 안에 들어간다. 그래서 점 하나를 3×3 칸으로
    평균한다 — 격자 경계에 놓인 지점이 한 칸 값에 좌우되지 않게 하기 위함이다.

    :raises SolarUnavailable: 자료가 없거나 영역 밖일 때
    """
    import numpy as np

    years = _load_kma()['years']
    if not years:
        raise SolarUnavailable(
            '기상청 일사량 자료가 없습니다 (backend/data/solar/kma_resource_map).')
    rc = _rowcol(lat, lng)
    if rc is None:
        raise SolarUnavailable('기상청 일사량 격자 범위를 벗어난 좌표입니다.')
    r, c = rc

    by_year: dict[int, float] = {}
    for y, arr in sorted(years.items()):
        w = arr[max(0, r - 1):r + 2, max(0, c - 1):c + 2]
        v = float(np.nanmean(w)) if np.isfinite(w).any() else float('nan')
        if v == v:                                              # NaN 아님
            by_year[y] = round(v, 1)
    if not by_year:
        raise SolarUnavailable('해당 좌표의 일사량 값이 비어 있습니다.')

    vals = list(by_year.values())
    mean = sum(vals) / len(vals)
    return {
        'dsr_w_m2': round(mean, 1),
        'by_year': by_year,
        'years': len(by_year),
        # 연간 변동폭 — 한 해만 보고 판단하지 않도록 함께 낸다
        'spread_w_m2': round(max(vals) - min(vals), 1),
        'ghi_kwh': round(mean * 8760 / 1000 * DSR_TO_GHI),
        'grid_m': int(_PIXEL),
    }


# ======================================================================
# ② Global Solar Atlas (250m 장기평균)
# ======================================================================
def gsa(lat: float, lng: float) -> dict | None:
    """
    Solargis 장기평균. 실패하면 None (판정을 막지 않는다).

        GHI  수평면 전일사량 kWh/m²/yr
        DNI  법선면 직달일사량
        PVOUT_csi  결정질 실리콘 기준 발전량 kWh/kWp/yr
        OPTA 최적 경사각(도)
    """
    params = {'loc': f'{lat:.4f},{lng:.4f}'}

    def call() -> dict:
        import httpx
        res = httpx.get(GSA_URL, params=params, timeout=45.0)
        res.raise_for_status()
        return res.json()

    try:
        payload = httpcache.get_or_set('gsa_lta', params, call)
    except Exception:                                           # noqa: BLE001
        logger.warning('Global Solar Atlas 조회 실패 %s,%s', lat, lng)
        return None

    a = ((payload or {}).get('annual') or {}).get('data') or {}
    if not a.get('GHI'):
        return None
    return {
        'ghi_kwh': round(float(a['GHI'])),
        # 최적경사면 일사량 — 발전량 산정의 출발점이다
        'gti_opta_kwh': round(float(a['GTI_opta'])) if a.get('GTI_opta') else None,
        'dni_kwh': round(float(a['DNI'])) if a.get('DNI') else None,
        'pvout_kwh_kwp': round(float(a['PVOUT_csi'])) if a.get('PVOUT_csi') else None,
        'optimal_tilt_deg': round(float(a['OPTA'])) if a.get('OPTA') else None,
    }


# ======================================================================
# ③ 기상청 ASOS 일사 관측 (실측)
# ======================================================================
def asos_gsr(lat: float, lng: float, years: int = 1) -> dict | None:
    """
    최근접 관측소의 **실측** 연간 일사량. 관측하지 않는 지점이면 다음 후보로.

    일사를 관측하는 ASOS 지점은 전국 30여 곳뿐이라 부지에서 수십 km 떨어질 수
    있다. 그래도 싣는 이유는 위성·모델값과 성격이 다른 **유일한 실측**이기
    때문이다. 거리를 함께 내보내 그대로 읽히지 않게 한다.
    """
    from .providers.wind import WindResourceProvider, nearest_stations, _haversine_km

    prov = WindResourceProvider(years=years)
    for st in nearest_stations(lat, lng, count=6):
        try:
            raw = prov._observe(st['stn_id'])                    # noqa: SLF001
        except Exception:                                       # noqa: BLE001
            continue
        if not raw or not raw.get('sum_gsr_mj'):
            continue                     # 이 지점은 일사를 관측하지 않는다
        # 관측 일수가 너무 적으면 연 합계로 볼 수 없다. 결측을 0으로 세면
        # 일사량이 낮은 지역인 것처럼 보인다.
        if raw['gsr_days'] < 300 * years:
            continue
        return {
            'station': st.get('name', ''),
            'station_id': st['stn_id'],
            'distance_km': round(_haversine_km(lat, lng, st['lat'], st['lng']), 1),
            'ghi_kwh': round(raw['sum_gsr_mj'] / years * MJ_TO_KWH),
            'days': raw['gsr_days'],
            'period': raw.get('period', ''),
        }
    return None


# ======================================================================
def summary() -> dict:
    """보유 현황 — 연동 현황 화면·운영 점검용."""
    years = _load_kma()['years']
    return {
        'kma_years': sorted(years),
        'grid_m': int(_PIXEL),
        'dir': KMA_DIR,
        'calibration': DSR_TO_GHI,
    }


# ======================================================================
# 발전량 — 일 평균 발전시간·이용률
# ======================================================================
#
# 일사량은 중간 지표다. 사업 판단에 쓰는 값은 **일 평균 발전시간**과
# **이용률**이다. 둘은 같은 수의 다른 표현이다.
#
#     일 평균 발전시간(h/일) = 연간 발전량(kWh/kWp) / 365
#     이용률(%)             = 연간 발전량(kWh/kWp) / 8,760
#
# ■ 왜 최적 경사각을 쓰지 않는가
#
# Global Solar Atlas가 주는 PVOUT은 **최적 경사각**(국내 31~35°) 기준이다.
# 실제 부지는 이격·음영·조성비 때문에 15~20°로 눕히는 경우가 많아, 최적값을
# 그대로 쓰면 사업성을 과대평가한다.
#
# 다만 손실은 생각보다 작다. PVGIS로 실측한 경사각 응답이다(홍성, PR 동일).
#
#     10° 92.3%   15° 95.0%   20° 97.1%   25° 98.7%   33°(최적) 100%
#
# 최적점 근처에서 곡선이 평탄해 13°를 눕혀도 3%만 줄어든다. 실무에서 눕히는
# 선택이 합리적인 근거가 이 숫자에 있다.
#
# ■ 일사량 절대값은 GSA를 쓴다
#
# 경사각 보정은 PVGIS로 하되 **비율만** 가져온다. PVGIS(ERA5)는 국내에서
# GSA·ASOS보다 4~5% 높게 나오는 경향이 있어 절대값으로 쓰면 그 편차가
# 발전량에 그대로 실린다. 비율은 그 편차에 영향받지 않는다.

#: 기본 모듈 경사각(도). 실무 통상 15~20°.
DEFAULT_TILT_DEG = 20
#: 기본 성능비(PR). 경사면 일사량 대비 실제 발전량의 비.
DEFAULT_PR = 0.84

#: 사내 기준 이용률 — **판정에 쓰지 않고 화면에 참고선으로만 긋는다.**
#: 계산값을 이 값에 맞추지 않는다. 둘을 나란히 놓고 부지가 기준선의 위인지
#: 아래인지를 보여주는 것이 목적이다.
INHOUSE_CF_PCT = 15.4
INHOUSE_HOURS_PER_DAY = 3.7

PVGIS_URL = 'https://re.jrc.ec.europa.eu/api/v5_2/PVcalc'


def _pvgis_gti(lat: float, lng: float, tilt: int) -> float | None:
    """PVGIS 경사면 연간 일사량(kWh/m²). 실패하면 None."""
    params = {'lat': round(lat, 3), 'lon': round(lng, 3), 'peakpower': 1,
              'loss': 14, 'angle': tilt, 'aspect': 0,
              'pvtechchoice': 'crystSi', 'mountingplace': 'free',
              'outputformat': 'json'}

    def call() -> dict:
        import httpx
        res = httpx.get(PVGIS_URL, params=params, timeout=60.0)
        res.raise_for_status()
        return res.json()

    try:
        d = httpcache.get_or_set('pvgis_pv', params, call)
        return float(d['outputs']['totals']['fixed']['H(i)_y'])
    except Exception:                                           # noqa: BLE001
        logger.warning('PVGIS 조회 실패 %s,%s tilt=%s', lat, lng, tilt)
        return None


def yield_estimate(lat: float, lng: float, tilt: int = DEFAULT_TILT_DEG,
                   pr: float = DEFAULT_PR, capacity_mw: float | None = None
                   ) -> dict | None:
    """
    일 평균 발전시간·이용률. 자료를 못 받으면 None.

    반환::

        {'gti_kwh',           경사면 연간 일사량 (tilt 기준)
         'yield_kwh_kwp',     연간 발전량
         'hours_per_day',     일 평균 발전시간
         'capacity_factor',   이용률(%)
         'tilt_deg', 'pr', 'optimal_tilt_deg', 'tilt_ratio',
         'annual_mwh',        설비용량을 주면 연간 발전량(MWh)
         'basis'}             산정 근거 문장
    """
    g = gsa(lat, lng)
    if not g or not g.get('optimal_tilt_deg'):
        return None
    opt = int(g['optimal_tilt_deg'])

    # 최적경사 GTI는 GSA 값을 쓰고, 실제 경사각으로의 감소분만 PVGIS 비율로 얻는다.
    gti_opt = g.get('gti_opta_kwh')
    if not gti_opt:
        return None

    ratio, note = 1.0, ''
    if tilt != opt:
        a, b = _pvgis_gti(lat, lng, tilt), _pvgis_gti(lat, lng, opt)
        if a and b:
            ratio = a / b
        else:
            note = ' (경사각 보정을 적용하지 못해 최적경사 기준입니다)'

    gti = gti_opt * ratio
    y = gti * pr
    out = {
        'gti_kwh': round(gti),
        'yield_kwh_kwp': round(y),
        'hours_per_day': round(y / 365, 2),
        'capacity_factor': round(y / 8760 * 100, 2),
        'tilt_deg': tilt,
        'pr': pr,
        'optimal_tilt_deg': opt,
        'tilt_ratio': round(ratio, 4),
        'inhouse_cf': INHOUSE_CF_PCT,
        'inhouse_hours': INHOUSE_HOURS_PER_DAY,
    }
    if capacity_mw:
        out['annual_mwh'] = round(capacity_mw * 1000 * y / 1000, 1)
    out['basis'] = (
        f'Global Solar Atlas 최적경사({opt}°) 일사량 {gti_opt:,.0f} kWh/m²를 '
        f'경사각 {tilt}°로 보정(×{ratio:.3f})한 {gti:,.0f} kWh/m²에 '
        f'성능비 {pr * 100:.0f}%를 적용한 값입니다.' + note)
    return out
