"""
풍황 어댑터 — 기상청 ASOS 관측자료
---------------------------------------------------------------
공공데이터포털 ASOS 일자료로 최근접 관측소의 연평균·최대 풍속을 산출한다.

  관측지점 목록  backend/data/kma/stations.json  (scripts/kma_station_probe.py 산출)
  관측 자료      https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList

⚠️ 이 값은 **판정 근거가 아니라 참고치**다. 이유는 셋이다.
  1) ASOS는 지상 10m 관측이다. 풍력 허브고도(100~140m)와 다르다
  2) 관측소는 대개 평지·시가지에 있고 부지는 산간 능선이라 지형이 다르다
  3) 연도별 편차가 커 1년 자료로 장기 평균을 대신할 수 없다
따라서 허브고도 환산은 **가정을 명시한 범위**로만 제시하고 단정하지 않는다.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import httpx
from django.conf import settings

from .. import httpcache
from ..schemas import AnalysisItem, Confidence, Difficulty, Status
from .base import LayerProvider, SiteQuery

logger = logging.getLogger(__name__)

ASOS_URL = 'https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList'
STATIONS_PATH = Path(settings.BASE_DIR) / 'data' / 'kma' / 'stations.json'

#: 후보 관측소 수 — 최근접만 보면 표고가 크게 다른 곳이 뽑힐 수 있다
CANDIDATE_COUNT = 5

#: 지표 거칠기(멱법칙 지수 α) 참고 범위.
#: 개활지 0.14 / 산림·복잡지형 0.25 — IEC·업계에서 통용되는 범위이며 법정 기준이 아니다.
ALPHA_OPEN = 0.14
ALPHA_FOREST = 0.25
#: 참고 환산 대상 허브고도(m)
HUB_HEIGHTS = (100, 140)

#: 육상풍력 입지 참고치 — 법정 기준이 아니다
REFERENCE_MS = 6.0


@lru_cache(maxsize=1)
def load_stations() -> list[dict]:
    """관측지점 목록. 파일이 없으면 빈 목록(→ UNKNOWN 판정)."""
    if not STATIONS_PATH.exists():
        logger.warning('관측지점 목록이 없습니다: %s', STATIONS_PATH)
        return []
    try:
        return json.loads(STATIONS_PATH.read_text(encoding='utf-8'))
    except Exception:                                           # noqa: BLE001
        logger.exception('관측지점 목록 판독 실패')
        return []


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_stations(lat: float, lng: float, count: int = CANDIDATE_COUNT) -> list[dict]:
    out = []
    for s in load_stations():
        out.append({**s, 'distance_km': round(_haversine_km(lat, lng, s['lat'], s['lng']), 1)})
    out.sort(key=lambda s: s['distance_km'])
    return out[:count]


class WindResourceProvider(LayerProvider):
    """풍황(연평균 풍속) — 기상청 ASOS 관측"""

    category = '사업성'
    item_name = '풍황(연평균 풍속)'
    data_source = '기상청 ASOS 일자료'
    required_settings = ('KMA_API_KEY',)
    default_law = '해당 없음 (비규제 · 사업성 판단 영역)'

    def __init__(self, station_id: str = '', years: int = 1):
        """station_id: 지점번호를 직접 지정하면 최근접 선정을 건너뛴다."""
        self.station_id = station_id
        self.years = max(1, min(5, years))

    # ------------------------------------------------------------------
    def analyze(self, q: SiteQuery) -> AnalysisItem:
        stations = load_stations()
        if not stations:
            return self.unknown(
                reason='기상청 관측지점 목록이 적재되지 않아 최근접 관측소를 선정하지 못했습니다.',
                action_required='`python scripts/kma_station_probe.py`로 지점 목록을 받으십시오. '
                                '(기상청 API허브 인증키와 "지상관측 지점정보" 활용신청 필요)',
            )

        if self.station_id:
            picked = next((s for s in stations if s['stn_id'] == str(self.station_id)), None)
            if not picked:
                return self.unknown(reason=f'지점번호 {self.station_id}를 찾지 못했습니다.')
            picked = {**picked,
                      'distance_km': round(_haversine_km(q.lat, q.lng,
                                                         picked['lat'], picked['lng']), 1)}
            candidates = [picked]
        else:
            candidates = nearest_stations(q.lat, q.lng)

        stats, used = None, None
        for cand in candidates:
            stats = self._observe(cand['stn_id'])
            if stats:
                used = cand
                break

        if not stats or not used:
            near = ', '.join(f'{c["name"]}({c["distance_km"]}km)' for c in candidates[:3])
            return self.unknown(
                reason=f'인근 관측소({near})에서 풍속 자료를 받지 못했습니다.',
                action_required='공공데이터포털 ASOS 일자료 활용신청 상태를 확인하십시오.',
            )

        return self._report(q, used, stats, candidates)

    # ------------------------------------------------------------------
    def _observe(self, stn_id: str) -> dict | None:
        """최근 N년 일자료 → 평균/최대 풍속"""
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=365 * self.years)
        params = {
            'serviceKey': settings.KMA_API_KEY,
            'pageNo': '1',
            'numOfRows': str(400 * self.years),
            'dataType': 'JSON',
            'dataCd': 'ASOS',
            'dateCd': 'DAY',
            'startDt': start.strftime('%Y%m%d'),
            'endDt': end.strftime('%Y%m%d'),
            'stnIds': str(stn_id),
        }

        def call() -> dict:
            res = httpx.get(ASOS_URL, params=params, timeout=90.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            return res.json()

        try:
            payload = httpcache.get_or_set('asos_daily', params, call)
        except Exception:                                       # noqa: BLE001
            logger.exception('ASOS 조회 실패 stn=%s', stn_id)
            return None

        try:
            body = payload['response']['body']
            items = body['items']['item']
        except (KeyError, TypeError):
            return None
        if isinstance(items, dict):
            items = [items]

        avg = [float(i['avgWs']) for i in items if _has(i, 'avgWs')]
        mx = [float(i['maxWs']) for i in items if _has(i, 'maxWs')]
        if not avg:
            return None
        return {
            'days': len(avg),
            'mean_ws': sum(avg) / len(avg),
            'max_ws': max(mx) if mx else None,
            'period': f"{params['startDt']}~{params['endDt']}",
        }

    # ------------------------------------------------------------------
    def _report(self, q: SiteQuery, st: dict, stats: dict,
                candidates: list[dict]) -> AnalysisItem:
        h_obs = st.get('anemometer_h_m') or 10.0
        mean = stats['mean_ws']

        # 허브고도 환산은 지표 거칠기 가정에 따라 크게 달라진다 → 범위로만 제시
        band = []
        for hub in HUB_HEIGHTS:
            lo = mean * (hub / h_obs) ** ALPHA_OPEN
            hi = mean * (hub / h_obs) ** ALPHA_FOREST
            band.append(f'{hub}m: {lo:.1f}~{hi:.1f}m/s')

        alt_gap = ''
        if st.get('alt_m') is not None:
            alt_gap = (f' 관측소 표고는 {st["alt_m"]:,.0f}m입니다 — '
                       '부지가 능선·고지대라면 실제 풍속은 이보다 상당히 높을 수 있습니다.')

        others = ', '.join(f'{c["name"]}({c["distance_km"]}km, 표고 {c.get("alt_m") or 0:,.0f}m)'
                           for c in candidates[1:4])
        alt_list = f' 인근 대안 관측소 — {others}.' if others else ''

        reason = (
            f'최근접 관측소 {st["name"]}({st["stn_id"]}) {st["distance_km"]}km, '
            f'관측기간 {stats["period"]} {stats["days"]}일 기준 '
            f'**연평균 풍속 {mean:.2f}m/s**(관측높이 {h_obs:.0f}m), '
            f'최대풍속 {stats["max_ws"]}m/s.'
            f'{alt_gap}'
            f' 허브고도 참고 환산(멱법칙, 지표 거칠기 α={ALPHA_OPEN}~{ALPHA_FOREST} 가정) — '
            f'{" / ".join(band)}.'
            f'{alt_list}'
        )

        return self.item(
            status=Status.UNKNOWN,
            reason=reason + (
                ' ※ 관측소와 부지는 지형·표고가 달라 이 값은 **참고치이며 판정 근거가 아닙니다.** '
                f'국내 육상풍력은 통상 연평균 {REFERENCE_MS}m/s 이상 지역에 입지하는 것으로 '
                '보고되나 이는 업계 참고치입니다.'
            ),
            difficulty=Difficulty.MEDIUM,
            confidence=Confidence.LOW,
            # 구조적 미확인 — 현장 계측 외에 대체 수단이 없다. 재시도해도 달라지지 않는다.
            unknown_reason='BY_DESIGN',
            source_url='https://data.kma.go.kr',
            action_required=(
                '① 사업 확정 전 현장 풍황계측(허브고도, 통상 1년 이상) 수행 '
                '② KIER 풍력자원지도로 광역 풍황 교차 확인 '
                '③ 계측 전까지는 발전량 추정에 이 값을 직접 쓰지 마십시오'
            ),
            raw={
                'station': {k: st.get(k) for k in
                            ('stn_id', 'name', 'lat', 'lng', 'alt_m',
                             'anemometer_h_m', 'distance_km')},
                'observation': stats,
                'hub_extrapolation': {
                    'method': 'power law',
                    'alpha_range': [ALPHA_OPEN, ALPHA_FOREST],
                    'heights_m': list(HUB_HEIGHTS),
                    'note': '가정에 따른 참고 범위이며 실측을 대체하지 않음',
                },
                'candidates': candidates,
            },
        )


def _has(item: dict, key: str) -> bool:
    v = item.get(key)
    if v in (None, ''):
        return False
    try:
        float(v)
    except (TypeError, ValueError):
        return False
    return True
