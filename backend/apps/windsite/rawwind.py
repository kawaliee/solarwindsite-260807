"""
기상청 재현바람장(Reanalysis Wind) 조회
---------------------------------------------------------------
「발전사업 세부허가기준 등에 관한 고시」 개정으로 풍력 발전사업허가에
풍황계측기 설치가 필수가 아니게 되고, 대신 **기상청 재현바람장 자료**를
제출하는 것으로 바뀐다. 이 모듈이 그 자료를 받아 온다.

  엔드포인트  https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-rawm_latlon_api
  인증키      settings.KMA_APIHUB_KEY (API허브. 공공데이터포털 키와 별개다)

기존 ASOS 풍황(`providers/wind.py`)과 무엇이 다른가 — 이것이 이 모듈을
따로 두는 이유다.

  ASOS        지상 10m 관측을 멱법칙으로 허브고도까지 **환산**한다.
              관측소는 평지·시가지에 있고 부지는 산간 능선이라 지형이 다르다.
  재현바람장   **부지 좌표 그대로, 80·140·220m 고도 그대로** 준다.
              환산 가정이 끼어들지 않는다.

⚠️ 실측으로 확인한 API의 성질 (2026-09)

  · 응답은 CSV다. `# 7777 START` ~ `# 7777 END` 사이에 `시각, 풍향, 풍속`.
  · itv는 **10 또는 30만** 유효하다. 20·40을 주면 `error (code: -9)`가 온다
    (문서의 "20:2일, 30:3일, 40:4일"은 itv 값이 아니라 조회 가능 일수 설명).
  · **느리다.** 응답 시간이 행 수에 거의 비례한다 —
      itv=30 1일(30행) 2.0초 · 3일(126행) 22.2초 · itv=10 1일(132행) 30.4초
    30분 간격으로도 1년치가 약 50분이다. 보고서 생성 중에 받을 수 없어
    **미리 받아 두고**(`collect_year`) 저장된 값을 쓴다.
  · 격자가 촘촘하다. 2.3km 떨어진 두 지점의 같은 시각 풍속이 1.0과 1.6m/s로
    달랐다(평창 문재풍력 1호기·8호기 실측). **한 지점 값을 사업지 전체로
    말하면 안 된다** — 대표 지점임을 반드시 밝힌다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = 'https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-rawm_latlon_api'

#: 조회 가능한 바람 고도(m). API가 이 넷만 받는다.
HEIGHTS = (10, 80, 140, 220)

#: 보고서에 싣는 고도. 육상풍력 허브고도가 대체로 이 사이에 든다.
REPORT_HEIGHTS = (80, 140)

#: 자료 간격(분). 10과 30만 유효하다(위 주석 참고).
#: 연평균을 낼 때는 30을 쓴다 — 10으로 받으면 세 배 느린데 평균은 거의 같다.
ITV_FINE = 10
ITV_COARSE = 30

#: 한 번에 요청할 일수. 3일이 22초로, 이보다 늘리면 타임아웃이 잦아진다.
CHUNK_DAYS = 3

#: 자료가 있는 기간(KST). API 안내문 기준이며, 벗어나면 빈 응답이 온다.
AVAILABLE_FROM = datetime(2021, 6, 1, 9, 0)
AVAILABLE_TO = datetime(2026, 6, 1, 8, 0)

_FMT = '%Y%m%d%H%M'


class RawWindError(RuntimeError):
    """재현바람장을 받지 못했다."""


@dataclass
class WindStats:
    """한 지점·한 고도의 풍황 통계."""

    lat: float
    lng: float
    height_m: int
    start: datetime
    end: datetime
    #: 유효 표본 수(결측 제외)
    samples: int
    mean_ms: float
    #: 풍속 구간별 시간 비율 — 발전 가능 구간을 가늠한다
    calm_ratio: float          # 3m/s 미만 (대개 컷인 미만)
    rated_ratio: float         # 12m/s 이상 (대개 정격 이상)
    max_ms: float
    #: 16방위 빈도 → 주풍향. 배치·이격 검토에 쓴다.
    prevailing_dir: str
    dir_ratio: float

    def to_dict(self) -> dict:
        return {
            'lat': self.lat, 'lng': self.lng, 'height_m': self.height_m,
            'start': self.start.strftime(_FMT), 'end': self.end.strftime(_FMT),
            'samples': self.samples,
            'mean_ms': round(self.mean_ms, 2),
            'calm_ratio': round(self.calm_ratio, 4),
            'rated_ratio': round(self.rated_ratio, 4),
            'max_ms': round(self.max_ms, 1),
            'prevailing_dir': self.prevailing_dir,
            'dir_ratio': round(self.dir_ratio, 4),
        }


#: 16방위. 재현바람장 풍향은 도(0~360, 북=0, 시계방향)로 온다.
_DIRS = ('N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
         'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW')
_DIR_KO = {'N': '북', 'NNE': '북북동', 'NE': '북동', 'ENE': '동북동',
           'E': '동', 'ESE': '동남동', 'SE': '남동', 'SSE': '남남동',
           'S': '남', 'SSW': '남남서', 'SW': '남서', 'WSW': '서남서',
           'W': '서', 'WNW': '서북서', 'NW': '북서', 'NNW': '북북서'}


def dir16(deg: float) -> str:
    """풍향(도) → 16방위 약어."""
    return _DIRS[int((deg % 360) / 22.5 + 0.5) % 16]


def dir_ko(code: str) -> str:
    return _DIR_KO.get(code, code)


def fetch(lat: float, lng: float, start: datetime, end: datetime,
          height_m: int = 80, itv: int = ITV_COARSE,
          timeout: float = 180.0) -> list[tuple[datetime, float, float]]:
    """
    한 구간의 재현바람장 → [(시각, 풍향deg, 풍속ms), …].

    결측(`-99` 등 음수 풍속)은 버린다. 빈 목록은 '그 구간에 자료가 없다'는
    뜻이지 오류가 아니다 — 호출부가 구간을 이어 붙이므로 예외로 올리지 않는다.
    """
    key = getattr(settings, 'KMA_APIHUB_KEY', '')
    if not key:
        raise RawWindError('KMA_APIHUB_KEY가 설정되지 않아 재현바람장을 '
                           '조회할 수 없습니다.')
    if height_m not in HEIGHTS:
        raise RawWindError(f'지원하지 않는 고도입니다: {height_m}m '
                           f'(가능: {", ".join(str(h) for h in HEIGHTS)})')

    params = {
        'tm1': start.strftime(_FMT), 'tm2': end.strftime(_FMT),
        'ht': str(height_m), 'lat': f'{lat:.5f}', 'lon': f'{lng:.5f}',
        'itv': str(itv), 'authKey': key,
    }
    res = httpx.get(BASE_URL, params=params, timeout=timeout,
                    headers={'User-Agent': 'windsite-feasibility/1.0'})
    res.raise_for_status()
    return _parse(res.text)


def _parse(body: str) -> list[tuple[datetime, float, float]]:
    """
    CSV 본문 → [(시각, 풍향, 풍속)].

    형식(실측)::

        # 7777 START
        # 37.5069, 128.2456
        #YYYYMMDDHHMI, wd80m, ws80m
        202409020900, 276.2,   1.7, =
        # 7777 END

    오류도 200으로 오고 본문에 `# user_input: error (code: -9)`가 실린다.
    그 경우 자료 줄이 하나도 없으므로 빈 목록이 된다.
    """
    out: list[tuple[datetime, float, float]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 3:
            continue
        try:
            t = datetime.strptime(parts[0], _FMT)
            wd = float(parts[1])
            ws = float(parts[2])
        except ValueError:
            continue
        # 결측은 음수로 온다. 0으로 세면 평균이 조용히 낮아진다.
        if ws < 0 or wd < 0:
            continue
        out.append((t, wd, ws))
    return out


def collect(lat: float, lng: float, start: datetime, end: datetime,
            height_m: int = 80, itv: int = ITV_COARSE,
            on_progress=None) -> list[tuple[datetime, float, float]]:
    """
    긴 구간을 CHUNK_DAYS씩 나눠 이어 받는다.

    한 조각이 실패해도 나머지는 살린다 — 1년치를 받다 한 번 끊겼다고 처음부터
    다시 하면 끝나지 않는다. 얼마나 받았는지는 표본 수로 드러난다.
    """
    start = max(start, AVAILABLE_FROM)
    end = min(end, AVAILABLE_TO)
    if start >= end:
        raise RawWindError(
            f'조회 가능 기간을 벗어났습니다 '
            f'({AVAILABLE_FROM:%Y-%m-%d} ~ {AVAILABLE_TO:%Y-%m-%d}).')

    rows: list[tuple[datetime, float, float]] = []
    cur = start
    total = max((end - start).days, 1)
    failed = 0
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        try:
            rows += fetch(lat, lng, cur, nxt - timedelta(minutes=itv),
                          height_m=height_m, itv=itv)
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            logger.warning('재현바람장 조각 실패 %s~%s (%dm): %s',
                           cur, nxt, height_m, e)
        cur = nxt
        if on_progress:
            on_progress(min((cur - start).days, total), total)
    if failed:
        logger.warning('재현바람장 %dm — 조각 %d개 실패', height_m, failed)
    return rows


def summarize(rows: list, lat: float, lng: float, height_m: int) -> WindStats | None:
    """시계열 → 통계. 표본이 없으면 None."""
    vals = [ws for _, _, ws in rows]
    if not vals:
        return None
    n = len(vals)
    mean = sum(vals) / n
    calm = sum(1 for v in vals if v < 3.0) / n
    rated = sum(1 for v in vals if v >= 12.0) / n

    bins: dict[str, int] = {}
    for _, wd, _ws in rows:
        d = dir16(wd)
        bins[d] = bins.get(d, 0) + 1
    top = max(bins.items(), key=lambda kv: kv[1]) if bins else ('', 0)

    return WindStats(
        lat=lat, lng=lng, height_m=height_m,
        start=rows[0][0], end=rows[-1][0],
        samples=n, mean_ms=mean,
        calm_ratio=calm, rated_ratio=rated, max_ms=max(vals),
        prevailing_dir=top[0], dir_ratio=(top[1] / n if n else 0.0),
    )


def shear_alpha(mean_low: float, h_low: int,
                mean_high: float, h_high: int) -> float | None:
    """
    두 고도의 평균 풍속에서 **연직시어 지수 α**를 역산한다.

    v2/v1 = (h2/h1)^α 이므로 α = ln(v2/v1) / ln(h2/h1).

    ASOS 환산이 α를 **가정**(개활지 0.14·산림 0.25)했던 것과 달리, 재현바람장은
    두 고도를 모두 주므로 그 부지의 실제 값을 낼 수 있다. 터빈 허브고도가
    조회 고도와 다를 때 이 값으로 환산하면 가정이 하나 줄어든다.
    """
    if not (mean_low and mean_high) or mean_low <= 0 or h_low <= 0:
        return None
    try:
        return math.log(mean_high / mean_low) / math.log(h_high / h_low)
    except (ValueError, ZeroDivisionError):
        return None
