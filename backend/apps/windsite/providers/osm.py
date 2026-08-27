"""
OpenStreetMap(Overpass) 기반 어댑터
---------------------------------------------------------------
한전은 변전소·송전선로 위치를 공개 API로 제공하지 않는다. 국내 OSM에는
변전소가 명칭·전압(voltage)과 함께 등재되어 있어 **계통 연계 후보점 탐색**의
1차 자료로 쓸 수 있다.

⚠️ 한계 — 결과 문구와 confidence에 반드시 반영한다.
   OSM은 시민 편집 데이터라 누락·오류가 있을 수 있고, 무엇보다
   **접속 가능 용량(여유도)은 담고 있지 않다.** 따라서 판정은
   "후보 탐색 결과"이며 한전 계통연계 사전검토를 대체하지 않는다.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse

import httpx
from django.conf import settings

from .. import geo, httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

DEFAULT_OVERPASS_URL = (
    'https://overpass-api.de/api/interpreter,'
    'https://overpass.kumi.systems/api/interpreter,'
    'https://overpass.private.coffee/api/interpreter'
)


def snap(lat: float, lng: float, grid_deg: float) -> tuple[float, float]:
    """
    조회 중심을 격자에 붙인다.

    배치선 11기는 서로 4km 안에 있는데, 변전소는 반경 30km로 찾는다. 두 원은
    95% 겹치지만 중심 좌표가 다르다는 이유로 캐시 키가 전부 달라져 같은 조회를
    11번 새로 했다. 공개 Overpass가 사용량 제한을 건 직접적 원인이다.

    **판정 정확도는 떨어지지 않는다.** 중심은 '어디를 훑을지'만 정하고, 실제
    거리는 부지 도형에서 다시 계산한다(_distance_m). 대신 격자로 옮긴 만큼
    반경을 넓혀, 붙이면서 훑는 범위가 좁아지는 일이 없게 한다.
    """
    return (round(lat / grid_deg) * grid_deg, round(lng / grid_deg) * grid_deg)


#: 격자 간격(도)과 그로 인한 최대 이동거리(m). 반경이 클수록 굵게 잡아도 된다.
#:
#: 변전소는 반경 30km로 찾고 결과를 거리순으로만 쓰므로, 훑는 범위가 조금 넓어도
#: 판정이 달라지지 않는다. 굵게 잡을수록 호기 간 캐시 공유가 커진다.
#: 0.05° ≈ 위도 5.5km — 대각선 절반이 최대 이동이라 4km를 더한다.
SUB_SNAP_DEG = 0.05
SUB_SNAP_MARGIN_M = 4000

#: 송전선로는 반경 10km. 중간 굵기로 둔다.
SNAP_GRID_DEG = 0.02
SNAP_MARGIN_M = 1600

#: 정온시설용 격자 — 반경이 2~3km로 작아 더 잘게 쓴다.
#: 0.005° ≈ 위도 555m, 절반이 최대 이동이므로 400m를 더한다.
QUIET_SNAP_DEG = 0.005
QUIET_SNAP_MARGIN_M = 400

#: 공개 인스턴스는 동시요청·빈도를 강하게 제한한다(429). 검토 1회에 여러 어댑터가
#: 동시에 호출하면 대부분 거절되므로 프로세스 단위로 직렬화한다.
_OVERPASS_LOCK = threading.Lock()
#: 429/504 재시도 대기 (초)
_RETRY_BACKOFF = (3, 8, 20)

#: 연속 실패가 이만큼 쌓이면 차단기를 연다.
#:
#: 공개 인스턴스가 사용량 제한을 걸면 그 뒤 호출도 거의 다 막힌다. 그런데
#: 호출 1건마다 재시도 4회 + 백오프 31초가 붙고 _OVERPASS_LOCK으로 직렬화까지
#: 되므로, 배치선 11기(호기당 3회 = 33회) 검토가 20분 넘게 갇힌다.
#: 실측에서 보고서 생성이 10분 넘게 끝나지 않은 원인이 이것이었다.
#:
#: 막힌 것이 확인되면 잠시 두드리기를 멈춘다. 해당 항목은 UNKNOWN(FETCH)이
#: 되는데, 그것이 정확한 상태다 — 데이터가 없는 게 아니라 조회하지 못한 것이고
#: 재시도하면 판정될 수 있다.
_BREAKER_THRESHOLD = 2
#: 차단 유지 시간(초). 공개 인스턴스의 제한 창이 대개 이 정도다.
_BREAKER_TTL = 180
_BREAKER_KEY = 'windsite:overpass:breaker'


def _breaker_open() -> bool:
    from django.core.cache import cache
    try:
        return int(cache.get(_BREAKER_KEY) or 0) >= _BREAKER_THRESHOLD
    except Exception:                                           # noqa: BLE001
        return False


def _breaker_hit() -> None:
    from django.core.cache import cache
    try:
        cache.set(_BREAKER_KEY, int(cache.get(_BREAKER_KEY) or 0) + 1, _BREAKER_TTL)
    except Exception:                                           # noqa: BLE001
        pass


def _breaker_reset() -> None:
    from django.core.cache import cache
    try:
        cache.delete(_BREAKER_KEY)
    except Exception:                                           # noqa: BLE001
        pass


class OverpassError(RuntimeError):
    """Overpass 호출 실패 — 데이터 부재와 구분하기 위한 예외"""


class OverpassClient:
    """Overpass QL 실행"""

    @staticmethod
    def endpoints() -> list[str]:
        """OVERPASS_URL은 쉼표로 여러 인스턴스를 지정할 수 있다."""
        raw = getattr(settings, 'OVERPASS_URL', '') or DEFAULT_OVERPASS_URL
        return [u.strip() for u in raw.split(',') if u.strip()]

    @classmethod
    def query(cls, ql: str, timeout: float = 120.0) -> list[dict]:
        return httpcache.get_or_set('overpass', {'ql': ql},
                                    lambda: cls._query_live(ql, timeout))

    @classmethod
    def _query_live(cls, ql: str, timeout: float) -> list[dict]:
        # 최근에 연달아 막혔으면 재시도하지 않고 즉시 실패로 돌린다.
        # 락 밖에서 먼저 보아, 줄 서서 기다리는 것 자체를 없앤다.
        if _breaker_open():
            raise OverpassError(
                'Overpass 사용량 제한이 확인되어 일시적으로 조회를 건너뜁니다 '
                f'({_BREAKER_TTL}초 후 자동 재개). 잠시 후 다시 시도하십시오.')

        endpoints = cls.endpoints()
        last = ''
        with _OVERPASS_LOCK:
            for attempt, wait in enumerate((0,) + _RETRY_BACKOFF):
                # 백오프는 **같은 서버를 다시 두드릴 때만** 필요하다. 아직 안 써본
                # 미러로 넘어가는 것이라면 기다릴 이유가 없다. 종전에는 미러를
                # 바꾸면서도 3·8·20초를 쉬어, 살아 있는 미러에 닿기까지 11초를
                # 헛되이 보냈다.
                if wait and attempt >= len(endpoints):
                    time.sleep(wait)
                url = endpoints[attempt % len(endpoints)]
                try:
                    res = httpx.post(
                        url,
                        content=urllib.parse.urlencode({'data': ql}),
                        headers={'Content-Type': 'application/x-www-form-urlencoded',
                                 'User-Agent': 'windsite-feasibility/1.0'},
                        timeout=timeout,
                    )
                except httpx.HTTPError as e:
                    last = f'{type(e).__name__}'
                    continue
                if res.status_code in (429, 504, 502, 503):
                    last = f'HTTP {res.status_code} (사용량 제한 또는 서버 과부하)'
                    continue
                if res.status_code >= 400:
                    raise OverpassError(f'HTTP {res.status_code}')
                try:
                    out = res.json().get('elements', [])
                except ValueError as e:
                    last = f'응답 파싱 실패: {type(e).__name__}'
                    continue
                _breaker_reset()
                return out
        # 재시도를 다 쓰고도 실패했다 — 차단기 카운트를 올린다
        _breaker_hit()
        raise OverpassError(last or '알 수 없는 오류')

    @staticmethod
    def center(el: dict) -> tuple[float, float] | None:
        c = el.get('center')
        if c:
            return c['lat'], c['lon']
        if el.get('lat') is not None:
            return el['lat'], el['lon']
        return None


def _distance_m(site, lat: float, lng: float) -> float:
    return site.distance(geo.point_metric(lat, lng))


# ======================================================================
class OsmGridProvider(LayerProvider):
    """전력계통 연계 — 변전소·송전선로 최근접 탐색"""

    category = '인프라'
    item_name = '전력계통 연계(변전소·송전선로)'
    data_source = '한전 분산전원 연계정보 + OpenStreetMap Overpass'
    required_settings = ()
    #: 없어도 OSM으로 변전소 위치는 찾는다. 있으면 여유용량까지 판정한다.
    optional_settings = ('KEPCO_API_KEY',)
    default_law = '송·배전용 전기설비 이용규정'

    #: 탐색 반경
    SUBSTATION_SEARCH_M = 30_000
    LINE_SEARCH_M = 10_000
    #: 사업성 참고 구간 — **법정 기준이 아니라 업계 통상 참고치**
    GOOD_KM = 10.0
    FAIR_KM = 25.0
    #: 풍력 계통연계에 통상 요구되는 전압 (V 단위, OSM voltage 태그)
    PREFERRED_VOLTAGE = 154_000

    def __init__(self, substations: list[dict] | None = None):
        """substations: [{'name','lat','lng','kv'}] — 사내 확보 좌표가 있으면 함께 반영"""
        self.manual = substations or []

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 계통 연계 거리를 산출하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )

        site = geo.point_metric(q.lat, q.lng)
        subs, sub_err = self._substations(q, site)
        lines, _ = self._lines(q, site)
        # 한전 여유용량을 변전소명으로 결합한다 (위치는 OSM, 용량은 한전)
        capacity, cap_err = self._capacities(q)
        self._merge_capacity(subs, capacity)

        if not subs:
            detail = (f'Overpass 조회에 실패했습니다 — {sub_err}. '
                      '데이터 부재가 아니라 조회 자체가 되지 않은 상태입니다.'
                      if sub_err else
                      f'반경 {self.SUBSTATION_SEARCH_M / 1000:.0f}km 내에서 변전소가 조회되지 '
                      '않았습니다. OSM 미등재일 수 있어 부재로 단정하지 않습니다.')
            return self.item(
                status=Status.UNKNOWN,
                reason=detail,
                difficulty=Difficulty.MEDIUM,
                confidence=Confidence.LOW,
                source_url='https://online.kepco.co.kr',
                action_required='한전ON에서 인근 변전소와 접속 가능 용량을 조회하고, '
                                '한전에 계통연계 사전검토(기술검토)를 신청하십시오.',
                raw={'substations': [], 'lines': lines[:10]},
            )

        nearest = subs[0]
        km = nearest['distance_m'] / 1000
        hv = [s for s in subs if (s.get('voltage') or 0) >= self.PREFERRED_VOLTAGE]
        nearest_hv = hv[0] if hv else None

        if km <= self.GOOD_KM and nearest_hv and nearest_hv['distance_m'] / 1000 <= self.GOOD_KM:
            st, df, msg = Status.POSSIBLE, Difficulty.LOW, '연계 여건이 양호한 편입니다.'
        elif km <= self.FAIR_KM:
            st, df, msg = (Status.CONDITIONAL, Difficulty.MEDIUM,
                           '송전선로 신설 거리가 있어 공사비·선로 인허가 부담이 발생합니다.')
        else:
            st, df, msg = (Status.CONDITIONAL, Difficulty.HIGH,
                           '연계점이 원거리에 있어 사업성 저하 요인입니다.')

        # 여유용량이 확인되면 거리보다 우선한다 — 아무리 가까워도 여유가 없으면 접속 불가
        cap_note, st, df = self._capacity_verdict(q, subs, capacity, cap_err, st, df)

        hv_txt = ''
        if nearest_hv:
            hv_txt = (f' 154kV 이상 변전소 중 최근접은 {nearest_hv["name"]}'
                      f'({nearest_hv["voltage"] // 1000}kV, '
                      f'{geo.format_distance(nearest_hv["distance_m"])})입니다.')
        # 직선거리만 내면 선로 길이를 과소평가한다. 실제 포설은 도로를 따라
        # 가므로 도로망 경로 거리를 나란히 낸다 — 다만 이것도 확정 경로가
        # 아니라(한전 협의로 정해진다) 둘을 함께 보여 준다.
        road_txt = ''
        rd = road_distance_m(q.lat, q.lng, nearest['lat'], nearest['lng'])             if nearest.get('lat') and nearest.get('lng') else None
        if rd:
            road_txt = (f' 도로망 경로로는 {geo.format_distance(rd)}입니다'
                        f'(직선 대비 {rd / max(nearest["distance_m"], 1):.1f}배) — '
                        '선로 포설 길이 산정에는 이 값을 참고하되, 실제 경로는 '
                        '한전 협의로 정해집니다.')
        line_txt = ''
        if lines:
            line_txt = (f' 반경 {self.LINE_SEARCH_M / 1000:.0f}km 내 송전선로 {len(lines)}건, '
                        f'최근접 {geo.format_distance(lines[0]["distance_m"])}'
                        f'({lines[0]["name"] or "명칭 미상"}).')

        return self.item(
            status=st,
            reason=(
                f'최근접 변전소는 {nearest["name"]}'
                f'({(nearest.get("voltage") or 0) // 1000 or "?"}kV)로 직선거리 '
                f'{geo.format_distance(nearest["distance_m"])}입니다.{road_txt}{hv_txt}{line_txt} {msg}'
                f'{cap_note}'
            ),
            difficulty=df,
            confidence=Confidence.MEDIUM if capacity else Confidence.LOW,
            source_url='https://bigdata.kepco.co.kr',
            action_required=(
                '① 한전에 계통연계 사전검토(기술검토) 신청 — 공표 여유용량은 신청 시점에 '
                '이미 선점되었을 수 있습니다 '
                '② 여유용량 부족 시 상위 전압 연계 또는 계통보강 일정 확인 '
                '③ 선로 경과지의 별도 인허가(선하지 보상·산지전용 등) 검토'
            ),
            # ⚠️ 도로망 경로 거리를 raw에도 남긴다. 종전에는 reason
            #    문자열에만 있어 보고서가 숫자로 쓸 수 없었다 —
            #    실무에서 선로 포설비를 가르는 것은 직선거리가 아니라
            #    이 값이다.
            raw={'substations': subs[:10], 'lines': lines[:10],
                 'road_distance_m': rd,
                 'search_radius_m': self.SUBSTATION_SEARCH_M,
                 'capacity_source': 'KEPCO 분산전원 연계정보' if capacity else '',
                 'capacity_error': cap_err},
        )

    # ------------------------------------------------------------------
    def _capacities(self, q: SiteQuery) -> tuple[dict[str, dict], str]:
        """
        한전 분산전원 연계정보 → {정규화 변전소명: 여유용량 집계}

        조회 지역(시도·시군구)은 중심 필지의 PNU 앞 5자리에서 얻는다.
        연속지적 응답은 캐시를 공유하므로 추가 호출 비용이 사실상 없다.
        """
        from .kepco import KepcoGridClient, KepcoGridError, normalize_substation
        from .ned import select_parcels

        if not getattr(settings, 'KEPCO_API_KEY', ''):
            return {}, ''

        pnu = ''
        try:
            parcels, _ = select_parcels(q, limit=1)
            pnu = parcels[0]['pnu'] if parcels else ''
        except Exception:                                       # noqa: BLE001
            logger.debug('중심 필지 PNU 확인 실패', exc_info=True)

        # ⚠️ 한전 API는 **행정구역 개편 전 코드**를 쓴다. 지적에서 받은 PNU는
        #    개편된 코드라 그대로 넘기면 404가 난다(실측: 장흥 12770 → 404,
        #    46880 → 27건). 확인된 대응표를 거쳐야 한다.
        #
        #    종전에는 이 404를 '호출 간격 제한'으로 안내했는데, 원인이 전혀
        #    달라 재시도만 되풀이하게 만들었다. 코드 불일치는 기다린다고
        #    풀리지 않는다.
        from .. import pnu as pnu_mod

        mapped = pnu_mod.for_ned(pnu)
        metro = mapped[:2] if len(mapped) >= 5 else ''
        city = mapped[2:5] if len(mapped) >= 5 else ''
        gap = pnu_mod.unmapped_reason(pnu, '한전 분산전원 연계정보')
        if gap:
            # 대응표가 없으면 **조회하지 않는다.** 개편 코드로 물으면 404가
            # 나고, 앞 5자리를 추측해 물으면 남의 지역 여유용량을 이 사업지
            # 것으로 싣게 된다.
            logger.info('한전 계통 조회 생략 — 코드 체계 불일치 (%s)', pnu[:5])
            return {}, gap
        try:
            rows = KepcoGridClient.fetch(metro_cd=metro, city_cd=city)
        except KepcoGridError as e:
            logger.warning('한전 계통 여유용량 조회 실패 — %s', e)
            return {}, str(e)
        except Exception as e:                                  # noqa: BLE001
            logger.exception('한전 계통 여유용량 조회 실패')
            return {}, type(e).__name__

        summary = KepcoGridClient.summarize(rows)
        return {normalize_substation(k): v for k, v in summary.items()}, ''

    @staticmethod
    def _merge_capacity(subs: list[dict], capacity: dict[str, dict]) -> None:
        """OSM 변전소 목록에 한전 여유용량을 붙인다 (명칭 정규화 후 대조)."""
        from .kepco import normalize_substation

        for s in subs:
            hit = capacity.get(normalize_substation(s['name']))
            if hit:
                s['margin_substation_kw'] = hit['substation_margin_kw']
                s['margin_line_kw'] = hit['best_line_margin_kw']
                s['best_line'] = hit['best_line']

    def _capacity_verdict(self, q: SiteQuery, subs: list[dict], capacity: dict,
                          cap_err: str, st: Status, df: Difficulty):
        """
        여유용량과 사업 용량을 비교한다.
        거리가 가까워도 여유가 없으면 접속할 수 없으므로 거리 판정을 덮어쓴다.
        """
        if cap_err:
            return (f' ⚠️ 한전 계통 여유용량 조회에 실패했습니다({cap_err}). '
                    '접속 가능 용량은 확인되지 않았습니다.'), st, df
        matched = [s for s in subs if 'margin_line_kw' in s]
        if not matched:
            return (' ⚠️ OSM 변전소명과 한전 자료를 대조하지 못해 '
                    '접속 가능 용량은 확인되지 않았습니다.'), st, df

        # 연계 대상은 **최근접 변전소**다. 여유용량이 가장 큰 곳을 고르면
        # 수십 km 밖 변전소를 연계점처럼 제시하게 되어 오도한다.
        best = min(matched, key=lambda s: s['distance_m'])
        need_kw = (q.capacity_mw or 0) * 1000

        detail = (f' 한전 분산전원 연계정보 기준 여유용량 — '
                  f'{best["name"]}({geo.format_distance(best["distance_m"])}): '
                  f'변전소 {best["margin_substation_kw"]:,.0f}kW · '
                  f'최대 선로({best["best_line"] or "-"}) {best["margin_line_kw"]:,.0f}kW '
                  '(단위는 문서에 미명시이며 kW로 해석했습니다).')

        # 최근접이 부족할 때만 여유가 더 큰 대안을 함께 알린다
        roomier = max(matched, key=lambda s: s['margin_line_kw'])
        if roomier is not best and roomier['margin_line_kw'] > best['margin_line_kw']:
            detail += (f' 참고로 {roomier["name"]}'
                       f'({geo.format_distance(roomier["distance_m"])})의 선로 여유가 '
                       f'{roomier["margin_line_kw"]:,.0f}kW로 더 큽니다.')

        if not need_kw:
            return detail, st, df

        if best['margin_substation_kw'] <= 0:
            # 배전선로에 여유가 남아 있어도 상위 변전소가 포화면 신규 접속이 막힌다.
            tail = ''
            if best['margin_line_kw'] > 0:
                tail = (f' (선로 여유 {best["margin_line_kw"]:,.0f}kW가 남아 있으나 '
                        '상위 변전소가 포화 상태입니다)')
            return (detail + f' **변전소 단위 여유용량이 0**이라 사업 용량 '
                    f'{need_kw:,.0f}kW의 신규 접속이 어렵습니다{tail}. '
                    '계통보강 계획과 대체 연계점을 확인해야 합니다.'), \
                Status.CONDITIONAL, Difficulty.CRITICAL
        if best['margin_line_kw'] < need_kw:
            return (detail + f' 사업 용량 {need_kw:,.0f}kW에 미치지 못해 '
                    '분할 연계 또는 상위 전압 연계 검토가 필요합니다.'), \
                Status.CONDITIONAL, Difficulty.HIGH
        return detail + ' 사업 용량을 수용할 여유가 확인됩니다.', st, df

    # ------------------------------------------------------------------
    def _substations(self, q: SiteQuery, site) -> tuple[list[dict], str]:
        # 반경 30km 조회를 호기마다 새로 하지 않도록 중심을 격자에 붙인다.
        # 넓은 반경일수록 이득이 크고 정확도 손실은 없다(거리는 따로 계산한다).
        clat, clng = snap(q.lat, q.lng, SUB_SNAP_DEG)
        r = self.SUBSTATION_SEARCH_M + SUB_SNAP_MARGIN_M
        ql = f"""[out:json][timeout:90];
(
  nwr["power"="substation"](around:{r},{clat},{clng});
);
out center tags;"""
        out: list[dict] = []
        error = ''
        try:
            for el in OverpassClient.query(ql):
                c = OverpassClient.center(el)
                if not c:
                    continue
                tags = el.get('tags') or {}
                out.append({
                    'name': tags.get('name') or '(명칭 미상)',
                    'voltage': _max_voltage(tags.get('voltage')),
                    'operator': tags.get('operator', ''),
                    'lat': c[0], 'lng': c[1],
                    'distance_m': round(_distance_m(site, *c), 1),
                    'osm': f'{el["type"]}/{el.get("id")}',
                })
        except OverpassError as e:
            error = str(e)
            logger.warning('Overpass 변전소 조회 실패 — %s', e)
        except Exception as e:                                  # noqa: BLE001
            error = type(e).__name__
            logger.exception('Overpass 변전소 조회 실패')

        for s in self.manual:
            try:
                out.append({
                    'name': f'{s.get("name", "수기 입력")} (사내 확보)',
                    'voltage': int(s.get('kv', 0)) * 1000,
                    'operator': '', 'lat': s['lat'], 'lng': s['lng'],
                    'distance_m': round(_distance_m(site, s['lat'], s['lng']), 1),
                    'osm': '',
                })
            except (KeyError, TypeError, ValueError):
                continue

        out.sort(key=lambda s: s['distance_m'])
        return out, error

    def _lines(self, q: SiteQuery, site) -> tuple[list[dict], str]:
        clat, clng = snap(q.lat, q.lng, SNAP_GRID_DEG)
        r = self.LINE_SEARCH_M + SNAP_MARGIN_M
        ql = f"""[out:json][timeout:90];
(
  way["power"="line"](around:{r},{clat},{clng});
);
out geom tags;"""
        out: list[dict] = []
        error = ''
        try:
            for el in OverpassClient.query(ql):
                pts = el.get('geometry') or []
                if len(pts) < 2:
                    continue
                tags = el.get('tags') or {}
                d = min(_distance_m(site, p['lat'], p['lon']) for p in pts)
                out.append({
                    'name': tags.get('name', ''),
                    'voltage': _max_voltage(tags.get('voltage')),
                    'distance_m': round(d, 1),
                    'osm': f'way/{el.get("id")}',
                })
        except OverpassError as e:
            error = str(e)
            logger.warning('Overpass 송전선로 조회 실패 — %s', e)
        except Exception as e:                                  # noqa: BLE001
            error = type(e).__name__
            logger.exception('Overpass 송전선로 조회 실패')
        out.sort(key=lambda s: s['distance_m'])
        return out, error


# ======================================================================
class QuietFacilityProvider(LayerProvider):
    """
    정온시설 동심원 분석 — 조례 이격거리와의 대조.

    지자체 조례(LocalOrdinance)에 등록된 이격거리를 반지름으로 하는 동심원을 그리고,
    각 링 안에 들어오는 정온시설 수와 최근접 거리를 산출한다.
    조례가 DB에 없으면 **거리만 제시하고 판정은 UNKNOWN으로 둔다.**
    """

    category = '지자체 조례'
    item_name = '정온시설 이격거리(동심원 분석)'
    data_source = 'OpenStreetMap Overpass + 내부 조례 DB'
    required_settings = ()
    default_law = '지자체 도시·군계획 조례 (이격거리)'

    #: 조례가 없을 때의 기본 탐색 반경 (판정이 아닌 '현황 제시'용)
    FALLBACK_SEARCH_M = 2000

    #: OSM 태그 → 정온시설 유형 (의미가 확정된 시설)
    AMENITY_TAGS = ('school', 'kindergarten', 'hospital', 'clinic', 'university',
                    'college', 'nursing_home', 'childcare', 'social_facility')
    #: 주거건물 — 이격거리 조례의 주된 대상은 '주거밀집지역'이다.
    #: amenity만 보면 학교·병원만 잡히고 정작 코앞의 아파트를 놓친다.
    RESIDENTIAL_BUILDINGS = ('residential', 'apartments', 'house', 'detached',
                             'semidetached_house', 'terrace', 'dormitory',
                             'bungalow', 'farm')

    def __init__(self, sido: str = '', sigungu: str = '',
                 energy: str = None):
        from .. import energy as energy_mod       # 지연 import (앱 로딩 순서)
        self.sido = sido
        self.sigungu = sigungu
        # 동심원 반지름이 곧 조례 이격거리다. 에너지원을 넘기지 않으면
        # 태양광 검토에 풍력 거리(예: 주거 2,000m)로 원이 그려진다.
        self.energy = energy_mod.normalize(energy)

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from .. import ordinances                               # 지연 import

        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 정온시설 이격거리를 산출하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )

        # DB에 없으면 자치법규 OPEN API로 그 자리에서 수집한다.
        # LocalOrdinanceProvider와 동시에 실행되므로 서비스 쪽에서 직렬화한다.
        rules = sorted(ordinances.ensure_ordinances(self.sido, self.sigungu,
                                                   self.energy),
                       key=lambda r: r.distance_m, reverse=True)

        search_m = max([r.distance_m for r in rules] + [self.FALLBACK_SEARCH_M])
        site = geo.point_metric(q.lat, q.lng)
        facilities, err = self._facilities(q, site, search_m)

        if err:
            return self.unknown(
                reason=(f'정온시설 조회(Overpass)에 실패했습니다 — {err}. '
                        '데이터 부재가 아니라 조회 자체가 되지 않은 상태입니다.'),
                action_required='잠시 후 재시도하거나, OVERPASS_URL에 자체/대체 인스턴스를 '
                                '지정하십시오. 현장 실사로 정온시설을 확인하십시오.',
                difficulty=Difficulty.HIGH,
                why='FETCH',
            )

        if not facilities:
            base = (f'반경 {search_m:,}m 내에서 학교·의료·복지 시설과 주거건물이 '
                    'OSM·V-World 건물 어느 쪽에서도 조회되지 않았습니다.')
            if not rules:
                return self.unknown(
                    reason=base + f' 또한 {self.sido} {self.sigungu} 이격거리 조례를 '
                                  '자동 조회했으나 이격거리 조항을 확인하지 못해 '
                                  '저촉 여부를 판정할 수 없습니다.',
                    action_required='자치법규정보시스템(elis.go.kr)에서 조례를 직접 확인해 등록하고, '
                                    '현장 실사로 정온시설을 확인하십시오.',
                )
            return self.item(
                status=Status.POSSIBLE,
                reason=base + ' 다만 OSM 미등재 시설이 있을 수 있어 현장 실사가 필요합니다.',
                difficulty=Difficulty.LOW,
                confidence=Confidence.LOW,
                action_required='주거밀집지역·정온시설을 현장 실사로 확인하십시오.',
                raw={'facilities': [], 'search_m': search_m,
                     'rings': [{'target': r.get_target_display(),
                                'distance_m': r.distance_m, 'count': 0} for r in rules]},
            )

        nearest = facilities[0]
        rings = [{
            'target': r.get_target_display(),
            'target_detail': r.target_detail,
            'distance_m': r.distance_m,
            'ordinance': f'{r.ordinance_name} {r.article}'.strip(),
            'count': sum(1 for f in facilities if f['distance_m'] <= r.distance_m),
            'violating': [f['name'] for f in facilities
                          if f['distance_m'] <= r.distance_m][:5],
            'confidence': r.confidence,
        } for r in rules]

        facils = [f for f in facilities if f.get('category') == 'FACILITY']
        homes = [f for f in facilities if f.get('category') == 'RESIDENTIAL']
        bldgs = [f for f in facilities if f.get('category') == 'BUILDING']
        parts = []
        if facils:
            n = facils[0]
            parts.append(f'정온시설 {len(facils)}개소(최근접 {n["name"]}·{n["kind"]} '
                         f'{geo.format_distance(n["distance_m"])})')
        if homes:
            n = homes[0]
            parts.append(f'주거건물 {len(homes)}동(최근접 {n["name"]} '
                         f'{geo.format_distance(n["distance_m"])})')
        if bldgs:
            n = bldgs[0]
            parts.append(f'용도 미확인 건물 {len(bldgs)}동(최근접 {n["name"]} '
                         f'{geo.format_distance(n["distance_m"])})')
        src = ''
        if bldgs:
            src = (f' 용도 미확인 {len(bldgs)}동은 V-World 건물 레이어에서만 확인된 것으로 '
                   '주거·창고·공공청사가 섞여 있을 수 있습니다. 동심원 집계에는 '
                   '배제할 근거가 없어 포함했습니다.')
        head = (
            f'반경 {search_m:,}m 내 ' + ' · '.join(parts) + '. '
            f'전체 최근접은 {nearest["name"]}({nearest["kind"]}) '
            f'{geo.format_distance(nearest["distance_m"])}입니다.' + src
        )

        if not rules:
            return self.item(
                status=Status.UNKNOWN,
                reason=head + f' {self.sido} {self.sigungu} 이격거리 조례를 자치법규 '
                              'OPEN API에서 자동 조회했으나 이격거리 조항을 찾지 못해 '
                              '저촉 여부는 판정하지 않았습니다.',
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 조례를 직접 확인하고, '
                                'sync_ordinances --sigungu <시군구> --apply 로 등록하십시오.',
                raw={'facilities': facilities[:20], 'rings': [], 'search_m': search_m},
            )

        breached = [r for r in rings if r['count'] > 0]
        low_conf = any(r.confidence == 'LOW' for r in rules)
        detail = ' / '.join(
            f'{r["target"]} {r["distance_m"]:,}m 이내 {r["count"]}개소' for r in rings)

        if breached:
            return self.item(
                status=Status.CONDITIONAL,
                reason=(
                    head + f' 조례 이격거리 동심원 분석 — {detail}. '
                    '이격거리 기준 내에 정온시설이 존재해 배치 조정 또는 예외 요건 검토가 필요합니다.'
                ),
                difficulty=Difficulty.HIGH,
                law=rules[0].ordinance_name,
                article=rules[0].article,
                confidence=Confidence.LOW if low_conf else Confidence.MEDIUM,
                source_url=rules[0].source_url,
                action_required=(
                    '① 발전기 배치를 조정해 이격거리 확보 가능 여부 검토 '
                    '② 조례상 완화·예외 규정 적용 가능성 확인 '
                    '③ 실측 거리는 개별 발전기 위치 기준으로 재산출 필요 '
                    '(본 분석은 대상 지점 1점 기준입니다)'
                ),
                raw={'facilities': facilities[:60], 'rings': rings, 'search_m': search_m},
            )

        return self.item(
            status=Status.POSSIBLE,
            reason=head + f' 조례 이격거리 동심원 분석 — {detail}. 기준 내 정온시설은 없습니다.',
            difficulty=Difficulty.LOW,
            law=rules[0].ordinance_name,
            article=rules[0].article,
            confidence=Confidence.LOW if low_conf else Confidence.MEDIUM,
            source_url=rules[0].source_url,
            action_required='개별 발전기 위치 확정 후 실측 거리로 재검증하십시오. '
                            'OSM 미등재 주거지가 있을 수 있어 현장 실사를 병행하십시오.',
            raw={'facilities': facilities[:60], 'rings': rings, 'search_m': search_m},
        )

    # ------------------------------------------------------------------
    def _facilities(self, q: SiteQuery, site, search_m: int) -> tuple[list[dict], str]:
        """
        정온시설·주거건물을 **두 소스에서** 모은다.

        OSM만 쓰면 산간·농어촌에서 통째로 빈다. 삼척시 근덕면 궁촌리에서
        OSM은 반경 2km에 0건이었는데 V-World 건물 레이어에는 주택 6동이 있었다.
        "OSM에 없음"을 "주거건물 없음"으로 보고하면 조례 저촉을 놓친다.

        OSM은 학교·병원 등 **용도**가 붙어 있고, V-World 건물은 **누락이 적다.**
        서로를 대체하지 않으므로 합치고 좌표로 중복을 제거한다.
        """
        osm_rows, err = self._from_osm(q, site, search_m)
        vw_rows = self._from_vworld(q, site, search_m)

        merged = list(osm_rows)
        # 같은 건물이 두 소스에 다 있으면 용도가 붙은 OSM 쪽을 남긴다
        occupied = {(round(f['lat'], 4), round(f['lng'], 4)) for f in osm_rows}
        for f in vw_rows:
            if (round(f['lat'], 4), round(f['lng'], 4)) in occupied:
                continue
            merged.append(f)
        merged.sort(key=lambda f: f['distance_m'])

        # V-World로 건물을 찾았다면 Overpass 실패는 치명적이지 않다
        if err and vw_rows:
            err = ''
        return merged, err

    # ------------------------------------------------------------------
    def _from_vworld(self, q: SiteQuery, site, search_m: int) -> list[dict]:
        """V-World 건물 레이어(lt_c_spbd) — OSM 공백을 메운다."""
        from .. import geo
        from .vworld import VworldClient

        layer_id = 'lt_c_spbd'
        try:
            from ..models import RegulationLayer
            row = RegulationLayer.objects.filter(code='건물', is_active=True).first()
            if row and row.layer_id:
                layer_id = row.layer_id
        except Exception:                                       # noqa: BLE001
            pass

        try:
            feats, _meta = VworldClient.fetch_all(layer_id, q.lat, q.lng, search_m)
        except Exception:                                       # noqa: BLE001
            logger.exception('V-World 건물 조회 실패')
            return []

        out: list[dict] = []
        for f in feats:
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            try:
                c = g.centroid
                gm = geo.to_metric(g)
                dist = site.distance(gm)
            except Exception:                                   # noqa: BLE001
                continue
            pr = f.get('properties') or {}
            name = (pr.get('buld_nm') or pr.get('buld_nm_dc') or '').strip()
            road = (pr.get('rd_nm') or '').strip()
            no = (pr.get('buld_no') or '').strip()
            if not name:
                name = f'{road} {no}'.strip() or '(건물명 미상)'
            floors = (pr.get('gro_flo_co') or '').strip()
            out.append({
                'name': name,
                'kind': f'건물{"·" + floors + "층" if floors else ""}',
                # V-World 건물 레이어는 용도를 주지 않는다. 주거로 단정하면
                # 구로구청 같은 공공청사가 '주거건물'로 집계된다. 별도 분류로 두고
                # 동심원 집계에는 포함하되(용도를 모르니 배제할 근거가 없다)
                # 요약 문구에서는 '용도 미확인 건물'로 구분해 적는다.
                'category': 'BUILDING',
                'lat': c.y, 'lng': c.x,
                'distance_m': round(dist, 1),
                'source': 'vworld',
            })
        return out

    # ------------------------------------------------------------------
    def _from_osm(self, q: SiteQuery, site, search_m: int) -> tuple[list[dict], str]:
        # 정온시설은 반경이 2~3km로 작아 스냅 이동이 상대적으로 크다.
        # 격자를 잘게 쓰고 그만큼만 반경을 넓힌다 — 좁아지면 시설을 놓친다.
        clat, clng = snap(q.lat, q.lng, QUIET_SNAP_DEG)
        search_m = int(search_m + QUIET_SNAP_MARGIN_M)
        pattern = '|'.join(self.AMENITY_TAGS)
        houses = '|'.join(self.RESIDENTIAL_BUILDINGS)
        ql = f"""[out:json][timeout:90];
(
  nwr["amenity"~"^({pattern})$"](around:{search_m},{clat},{clng});
  nwr["healthcare"](around:{search_m},{clat},{clng});
  nwr["building"~"^({houses})$"](around:{search_m},{clat},{clng});
);
out center tags;"""
        out: list[dict] = []
        try:
            elements = OverpassClient.query(ql)
        except OverpassError as e:
            logger.warning('Overpass 정온시설 조회 실패 — %s', e)
            return out, str(e)
        except Exception as e:                                  # noqa: BLE001
            logger.exception('Overpass 정온시설 조회 실패')
            return out, type(e).__name__

        seen: set[str] = set()
        for el in elements:
            c = OverpassClient.center(el)
            if not c:
                continue
            tags = el.get('tags') or {}
            building = tags.get('building') or ''
            kind = (tags.get('amenity') or tags.get('healthcare')
                    or (f'주거({building})' if building else '기타'))
            name = tags.get('name') or tags.get('amenity') or building or '(명칭 미상)'
            key = f'{name}:{round(c[0], 5)},{round(c[1], 5)}'
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'name': name,
                'kind': kind,
                # 학교·병원은 유형이 확정된 정온시설, 주거건물은 조례상 '주거밀집지역'
                # 판단 대상 — 성격이 달라 결과에서 구분해 제시한다
                'category': 'RESIDENTIAL' if building else 'FACILITY',
                'lat': c[0], 'lng': c[1],
                'distance_m': round(_distance_m(site, *c), 1),
                'osm': f'{el["type"]}/{el.get("id")}',
                'source': 'osm',
            })
        out.sort(key=lambda f: f['distance_m'])
        return out, ''


# ----------------------------------------------------------------------
def _max_voltage(raw: str | None) -> int:
    """OSM voltage 태그는 '345000;154000' 형태일 수 있다."""
    if not raw:
        return 0
    best = 0
    for part in str(raw).replace(',', ';').split(';'):
        try:
            best = max(best, int(float(part.strip())))
        except ValueError:
            continue
    return best


# ======================================================================
# 도로 기준 거리
# ======================================================================
#
# 계통 연계 선로는 **도로를 따라** 포설한다. 직선거리만 보면 선로 길이를
# 30~40% 과소평가하고, 그 차이가 공사비에 그대로 실린다(실측: 홍성 구역 →
# 인근 지점 직선 6.6km 대비 도로 9.4km).
#
# 다만 도로거리도 **확정 경로가 아니다.** 실제 포설 경로는 한전 협의로
# 정해지므로 직선거리와 나란히 내고, 조회에 실패하면 직선거리만 낸다.

ROUTE_URL = 'https://router.project-osrm.org/route/v1/driving/{}'


def road_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float | None:
    """
    두 지점 사이의 **도로망 경로 거리**(m). 실패하면 None.

    실패를 예외로 올리지 않는다 — 계통 항목 전체를 죽일 이유가 없다.
    """
    coords = f'{lng1:.5f},{lat1:.5f};{lng2:.5f},{lat2:.5f}'
    params = {'overview': 'false'}

    def call() -> dict:
        res = httpx.get(ROUTE_URL.format(coords), params=params, timeout=30.0)
        res.raise_for_status()
        return res.json()

    try:
        d = httpcache.get_or_set('osrm_route', {'c': coords}, call)
        routes = (d or {}).get('routes') or []
        return float(routes[0]['distance']) if routes else None
    except Exception:                                           # noqa: BLE001
        logger.info('도로 경로 조회 실패 %s', coords)
        return None
