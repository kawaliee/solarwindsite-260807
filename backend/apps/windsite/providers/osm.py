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

DEFAULT_OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

#: 공개 인스턴스는 동시요청·빈도를 강하게 제한한다(429). 검토 1회에 여러 어댑터가
#: 동시에 호출하면 대부분 거절되므로 프로세스 단위로 직렬화한다.
_OVERPASS_LOCK = threading.Lock()
#: 429/504 재시도 대기 (초)
_RETRY_BACKOFF = (3, 8, 20)


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
        endpoints = cls.endpoints()
        last = ''
        with _OVERPASS_LOCK:
            for attempt, wait in enumerate((0,) + _RETRY_BACKOFF):
                if wait:
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
                    return res.json().get('elements', [])
                except ValueError as e:
                    last = f'응답 파싱 실패: {type(e).__name__}'
                    continue
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
    data_source = 'OpenStreetMap Overpass + 수기 입력'
    required_settings = ()
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
                f'{geo.format_distance(nearest["distance_m"])}입니다.{hv_txt}{line_txt} {msg}'
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
            raw={'substations': subs[:10], 'lines': lines[:10],
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

        metro = pnu[:2] if len(pnu) >= 5 else ''
        city = pnu[2:5] if len(pnu) >= 5 else ''
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

        best = max(matched, key=lambda s: s['margin_line_kw'])
        need_kw = (q.capacity_mw or 0) * 1000

        detail = (f' 한전 분산전원 연계정보 기준 여유용량 — '
                  f'{best["name"]}: 변전소 {best["margin_substation_kw"]:,.0f}kW · '
                  f'최대 선로({best["best_line"] or "-"}) {best["margin_line_kw"]:,.0f}kW '
                  '(단위는 문서에 미명시이며 kW로 해석했습니다).')

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
        r = self.SUBSTATION_SEARCH_M
        ql = f"""[out:json][timeout:90];
(
  nwr["power"="substation"](around:{r},{q.lat},{q.lng});
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
        r = self.LINE_SEARCH_M
        ql = f"""[out:json][timeout:90];
(
  way["power"="line"](around:{r},{q.lat},{q.lng});
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

    #: OSM 태그 → 정온시설 유형
    AMENITY_TAGS = ('school', 'kindergarten', 'hospital', 'clinic', 'university',
                    'college', 'nursing_home', 'childcare', 'social_facility')

    def __init__(self, sido: str = '', sigungu: str = ''):
        self.sido = sido
        self.sigungu = sigungu

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        from ..models import LocalOrdinance                     # 지연 import

        if not geo.GEO_AVAILABLE:
            return self.unknown(
                reason='공간연산 라이브러리가 없어 정온시설 이격거리를 산출하지 못했습니다.',
                action_required='requirements.txt 반영 후 backend 이미지를 재빌드하십시오.',
            )

        rules = []
        if self.sigungu:
            qs = LocalOrdinance.objects.filter(
                sigungu=self.sigungu, energy_type__in=['WIND', 'ALL'])
            if self.sido:
                qs = qs.filter(sido=self.sido)
            rules = list(qs.order_by('-distance_m'))

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
            )

        if not facilities:
            base = (f'반경 {search_m:,}m 내에서 학교·의료·복지 계열 정온시설이 '
                    'OSM에 등재된 것이 없습니다.')
            if not rules:
                return self.unknown(
                    reason=base + f' 또한 {self.sido} {self.sigungu} 이격거리 조례가 '
                                  'DB에 등록되어 있지 않아 저촉 여부를 판정할 수 없습니다.',
                    action_required='자치법규정보시스템(elis.go.kr)에서 조례를 확인해 등록하고, '
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

        head = (
            f'반경 {search_m:,}m 내 정온시설 {len(facilities)}개소. 최근접은 '
            f'{nearest["name"]}({nearest["kind"]}) {geo.format_distance(nearest["distance_m"])}입니다.'
        )

        if not rules:
            return self.item(
                status=Status.UNKNOWN,
                reason=head + f' {self.sido} {self.sigungu} 이격거리 조례가 DB에 없어 '
                              '저촉 여부는 판정하지 않았습니다.',
                difficulty=Difficulty.HIGH,
                confidence=Confidence.LOW,
                action_required='자치법규정보시스템(elis.go.kr)에서 해당 지자체 조례를 확인해 '
                                'LocalOrdinance에 등록하십시오.',
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
        pattern = '|'.join(self.AMENITY_TAGS)
        ql = f"""[out:json][timeout:90];
(
  nwr["amenity"~"^({pattern})$"](around:{search_m},{q.lat},{q.lng});
  nwr["healthcare"](around:{search_m},{q.lat},{q.lng});
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
            name = tags.get('name') or tags.get('amenity') or '(명칭 미상)'
            key = f'{name}:{round(c[0], 5)},{round(c[1], 5)}'
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'name': name,
                'kind': tags.get('amenity') or tags.get('healthcare') or '기타',
                'lat': c[0], 'lng': c[1],
                'distance_m': round(_distance_m(site, *c), 1),
                'osm': f'{el["type"]}/{el.get("id")}',
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
