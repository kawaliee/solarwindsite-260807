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

#: 한 조각 안에서 이어받기를 시도할 최대 횟수. 응답이 끝에서 잘리므로
#: 몇 번 더 청해야 한 조각이 채워진다. 진전이 없으면 즉시 접는다.
MAX_RESUME = 8

#: 판정에 쓸 수 있는 최소 수집률. 조각이 무더기로 실패하면 특정 기간이
#: 통째로 빠져 계절 편향이 생긴다. `RawWindProvider.MIN_COVERAGE`가 이 값을
#: 쓴다 — 수집과 판정이 다른 하한을 보면 저장은 됐는데 판정은 거부하는 식으로
#: 어긋난다.
MIN_USABLE_COVERAGE = 0.7

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
    step = timedelta(minutes=itv)
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        # ⚠️ 응답이 요청 구간을 다 채우지 않는다. 실측(140m·30분 간격·3일):
        #    112/144행 · 121/144행 · 144/144행 — 중간에 끊긴 데 없이 **끝이
        #    잘린다**. 서버가 응답 크기에서 자르는 것으로 보인다.
        #
        #    한 번만 받고 넘어가면 늘 조각의 **뒷부분**이 빠진다. 조각 시작이
        #    항상 0시라 빠지는 자리가 매번 셋째 날 밤 시간대로 몰려, 결측이
        #    고르게 흩어지지 않고 **일주기 편향**이 된다. 밤에 바람이 센
        #    지점이면 연평균이 낮게 나온다 — 1년치 87.9%가 그렇게 생겼다.
        #
        #    그래서 받은 마지막 시각 다음부터 이어서 다시 청한다. 진전이
        #    없으면(같은 자리에서 또 끊기면) 그 조각을 접고 다음으로 간다.
        want_last = nxt - step
        sub_cur, guard = cur, 0
        while sub_cur <= want_last and guard < MAX_RESUME:
            try:
                got = fetch(lat, lng, sub_cur, want_last,
                            height_m=height_m, itv=itv)
            except Exception as e:                              # noqa: BLE001
                failed += 1
                logger.warning('재현바람장 조각 실패 %s~%s (%dm): %s',
                               sub_cur, want_last, height_m, e)
                break
            if not got:
                break
            rows += got
            last = got[-1][0]
            if last >= want_last or last < sub_cur:
                break
            sub_cur = last + step
            guard += 1
            logger.info('재현바람장 이어받기 %s~%s (%dm)',
                        sub_cur, want_last, height_m)
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


# ── 육상풍력 사업 유효지역 (고시 제5조 관련 별표) ─────────────────────
#: 「발전사업세부허가기준, 전기요금산정기준, 전력량계허용오차 및 전력계통운영업무에 관한 고시」(law.go.kr 행정규칙 일련번호 2100000274032) — **육상풍력 사업 유효지역**(풍력발전사업 허가를 받을 수
#: 있는 지역)은 *신청좌표를 중심으로 반지름을 2km로 하는 원 이내로 해역을
#: 제외한 지역*으로 한다.
VALID_RADIUS_M = 2000

#: 위 고시의 law.go.kr 행정규칙 일련번호. 조문 대조에 쓴다.
NOTICE_MST = '2100000274032'

#: ⚠️ 판정 대상은 호기의 **점**이 아니다. 같은 고시가 이어서 정한다 —
#: *풍력발전기 블레이드의 회전 가능 범위를 수평으로 투영한 면적은 유효지역
#: 이내여야 한다.* 그러므로 실제 조건은
#:
#:     신청좌표~호기 거리 + 로터 반지름  ≤  2,000m
#:
#: 로터를 빼고 재면 여유를 로터 반지름만큼 과대평가한다. 실측에서 완도 10기는
#: 최원 1,876m라 점으로는 통과하지만, 로터 반지름 200m를 더하면 2,076m로
#: **유효지역을 벗어난다** — 빼고 재면 통과로 잘못 읽는다.
#:
#: 기본값 200m는 국내 육상풍력에서 나올 수 있는 블레이드 회전 반지름의
#: **상한**이다. 기종이 확정되기 전에는 상한으로 재야 안전하다 — 실제보다
#: 작게 잡으면 유효지역에 든다고 했다가 기종 확정 후 벗어난다. 기종이
#: 정해지면 settings.WINDSITE_ROTOR_RADIUS_M 으로 덮어쓴다
#: (`DEFAULT_TIP_HEIGHT_M`과 같은 방식).
DEFAULT_ROTOR_RADIUS_M = 200


def rotor_radius_m() -> float:
    from django.conf import settings
    return float(getattr(settings, 'WINDSITE_ROTOR_RADIUS_M', None)
                 or DEFAULT_ROTOR_RADIUS_M)


def _metric(pts):
    from . import geo
    return [geo.point_metric(float(a), float(b)) for a, b in pts]


def _reach(mp, radius_m: float, rotor_m: float) -> list[set]:
    """지점 i를 신청좌표로 잡았을 때 유효지역에 드는 호기 집합."""
    lim = radius_m - rotor_m
    return [{j for j, o in enumerate(mp) if c.distance(o) <= lim} for c in mp]


def best_center(pts, radius_m: float = VALID_RADIUS_M,
                rotor_m: float | None = None) -> dict | None:
    """
    **가장 많은 호기를 유효지역 안에 담는 호기**를 신청좌표 후보로 고른다.

    배치선의 기하 중심을 잡으면 될 것 같지만 호기 자리를 쓴다. 재현바람장은
    격자 조회라 아무 좌표나 되긴 하지만, 신청좌표를 실제 호기 자리에 두면
    **그 호기의 풍황**이라고 말할 수 있고 유효지역 판정과 풍황 자료의 기준점이
    한 점으로 정리된다.

    같은 개수를 담는 호기가 여럿이면 **여유가 큰**(가장 먼 호기까지의 거리가
    짧은) 쪽을 고른다. 설계 단계에서 배치가 조금 움직여도 유효지역이 깨지지
    않는다 — 평창 실측에서 3·4·5·6·7호기가 모두 8기를 담지만 여유는 4호기가
    가장 크다.

    반환: {index, lat, lng, covered, uncovered, max_dist_m, margin_m, rotor_m}
      · max_dist_m  신청좌표~가장 먼 호기 (로터 미포함, 전체 호기 기준)
      · margin_m    2,000m까지 남은 여유 = radius − (담긴 것 중 최원거리 + 로터)
                    담기지 않은 호기가 있으면 음수로 얼마나 모자라는지 말한다
    """
    pts = [(float(a), float(b)) for a, b in (pts or [])]
    if not pts:
        return None
    rotor = rotor_radius_m() if rotor_m is None else float(rotor_m)
    mp = _metric(pts)
    best = None
    for i, c in enumerate(mp):
        d = [c.distance(o) for o in mp]
        covered = [j for j, x in enumerate(d) if x + rotor <= radius_m]
        key = (len(covered), -max(d))
        if best is None or key > best[0]:
            best = (key, i, covered, d)
    _k, i, covered, d = best
    inner = max((d[j] for j in covered), default=0.0)
    margin = radius_m - (max(d) + rotor) if len(covered) == len(pts)         else radius_m - (max(d) + rotor)
    return {'index': i, 'lat': pts[i][0], 'lng': pts[i][1],
            'covered': covered,
            'uncovered': [j for j in range(len(pts)) if j not in set(covered)],
            'max_dist_m': round(max(d), 1),
            'inner_dist_m': round(inner, 1),
            'margin_m': round(margin, 1),
            'rotor_m': rotor}


def free_center(pts, radius_m: float = VALID_RADIUS_M,
                rotor_m: float | None = None) -> dict | None:
    """
    호기 자리에 매이지 않는 **기하학적 최적 신청좌표**(최소외접원 중심).

    신청좌표는 호기 좌표일 필요가 없다. 호기 중심으로 안 되는 배치도 자유
    좌표로는 되는 경우가 있어, **정말 유효지역 하나로 안 되는 사업인지**를
    가르려면 이쪽을 봐야 한다. 이것으로도 안 되면 배치를 바꾸거나 사업을
    나누는 수밖에 없다 — 설계 단계에서 알아야 할 사실이다.
    """
    from shapely import minimum_bounding_circle
    from shapely.geometry import MultiPoint

    from . import geo
    pts = [(float(a), float(b)) for a, b in (pts or [])]
    if not pts:
        return None
    rotor = rotor_radius_m() if rotor_m is None else float(rotor_m)
    mp = _metric(pts)
    c = minimum_bounding_circle(MultiPoint(mp)).centroid
    d = [c.distance(o) for o in mp]
    lng, lat = geo.to_geographic_xy(c.x, c.y)

    # ⚠️ 기하학적 최적점이 **바다에 떨어질 수 있다.** 실측에서 완도 10기의
    #    최소외접원 중심은 해안선에서 132m 떨어진 해상이었다. 육상풍력
    #    신청좌표를 해상에 두는 것은 말이 되지 않으므로, 육지 여부를 함께
    #    낸다. 조회에 실패하면 None으로 두고 '확인 필요'로 다룬다 —
    #    모르는 것을 육지로 갈음하지 않는다.
    on_land, offshore = None, None
    try:
        from . import coast
        pt = geo.point_metric(lat, lng)
        land = coast.land_union(pt.buffer(3000))
        on_land = bool(land.contains(pt))
        offshore = 0.0 if on_land else round(float(pt.distance(land)), 1)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('자유 신청좌표 육지 확인 실패: %s', e)

    return {'lat': round(lat, 6), 'lng': round(lng, 6),
            'max_dist_m': round(max(d), 1),
            'margin_m': round(radius_m - (max(d) + rotor), 1),
            'fits': max(d) + rotor <= radius_m,
            'on_land': on_land,
            'offshore_m': offshore,
            'usable': (max(d) + rotor <= radius_m) and on_land is True,
            'rotor_m': rotor}


def cover_points(pts, radius_m: float = VALID_RADIUS_M,
                 rotor_m: float | None = None) -> list[dict]:
    """
    호기 전부를 덮는 데 필요한 **신청좌표 목록**(탐욕적 집합 덮기).

    배치선이 유효지역보다 길면 한 좌표로는 안 된다 — 삼척 22기는 배치선이 약
    7km라 어느 호기를 골라도 최대 11기까지만 담긴다(실측). 유효지역이 곧
    발전사업허가의 단위이므로, 이 개수는 **허가를 몇 건으로 나눠야 하는가**를
    뜻한다. 재현바람장도 좌표마다 따로 받아야 한다.

    최소 개수를 보장하지는 않는다(집합 덮기는 NP-난해). 좌표 하나에 한 시간
    가까이 걸리는 수집이라 개수를 사람이 보고 판단하는 것이 중요하지, 최적해를
    다투는 실익은 없다.
    """
    pts = [(float(a), float(b)) for a, b in (pts or [])]
    if not pts:
        return []
    rotor = rotor_radius_m() if rotor_m is None else float(rotor_m)
    mp = _metric(pts)
    reach = _reach(mp, radius_m, rotor)
    rest, out = set(range(len(pts))), []
    while rest:
        i = max(range(len(pts)), key=lambda k: (len(reach[k] & rest), -k))
        got = sorted(reach[i] & rest)
        if not got:                       # 있을 수 없지만 무한 루프는 막는다
            break
        out.append({'index': i, 'lat': pts[i][0], 'lng': pts[i][1],
                    'assigned': got})
        rest -= reach[i]
    return out
