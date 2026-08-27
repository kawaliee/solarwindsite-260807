"""
필지(PNU) 단위 조회
---------------------------------------------------------------
태양광은 사업 단위가 **필지**다. 풍력이 호기 좌표를 찍는 자리에서, 태양광은
"이 필지가 되는가"를 묻는다. 그래서 클릭 좌표를 그 좌표가 놓인 **필지
폴리곤**으로 바꿔 주는 계층이 필요하다.

■ 왜 좌표를 그대로 쓰지 않는가

점+반경으로 검토하면 원이 필지 경계를 넘나든다. 옆 필지의 규제가 딸려
들어오고, 정작 이 필지의 끝자락은 빠진다. 면적도 원 면적이라 사업 규모와
무관한 숫자가 된다. 필지 경계로 판정해야 "이 땅에 얼마를 지을 수 있는가"에
답할 수 있다.

■ 조회 방식 (서버 실측)

V-World 연속지적(`lp_pa_cbnd_bubun`)에 `geomFilter=POINT(...)`로 묻는다.
**buffer=0은 0건을 돌려준다**(실측). 작은 버퍼를 줘 후보를 받은 뒤,
그중 클릭 지점을 **실제로 포함하는** 폴리곤을 고른다.

경계선을 정확히 눌러 어느 폴리곤도 포함하지 않는 경우가 있다. 그때는 가장
가까운 필지를 돌려주되 `exact=False`로 표시한다 — 추측으로 고른 필지를
확정인 것처럼 보여 주면, 남의 땅 판정을 이 사업지 것으로 읽게 된다.
"""
from __future__ import annotations

import logging

from . import geo
from .providers.cadastral import parse_jimok
from .providers.vworld import VworldClient

logger = logging.getLogger(__name__)

#: 연속지적 레이어. RegulationLayer(DB)에 등록돼 있으면 그 값이 우선한다.
LAYER_CODE = '연속지적'
FALLBACK_LAYER_ID = 'lp_pa_cbnd_bubun'

#: 클릭 지점 주변 조회 버퍼(m). 0이면 V-World가 0건을 준다(실측).
#: 너무 키우면 옆 필지가 잔뜩 딸려 와 포함 판정 비용만 늘어난다.
POINT_BUFFER_M = 10

#: 경계를 눌러 포함 필지가 없을 때, 이 거리 안의 필지만 대안으로 인정한다.
#: 이보다 멀면 클릭이 빗나간 것으로 보고 아무것도 돌려주지 않는다.
MAX_SNAP_M = 15

#: 한 번에 검토할 수 있는 필지 수. 인접 필지를 묶어 한 사업지로 보는 용도라
#: 이 이상은 구역(폴리곤) 검토로 다루는 편이 맞다.
MAX_PARCELS = 50


class ParcelLookupError(RuntimeError):
    """필지를 조회하지 **못했다**. '필지가 없다'와 구분하기 위한 예외."""


def _layer_id() -> str:
    from .models import RegulationLayer                          # 지연 import
    lyr = RegulationLayer.objects.filter(code=LAYER_CODE, is_active=True).first()
    return lyr.layer_id if lyr else FALLBACK_LAYER_ID


def lookup(lat: float, lng: float) -> dict | None:
    """
    클릭 좌표가 놓인 필지 하나를 돌려준다. 해당 필지가 없으면 None.

    반환::

        {'pnu', 'addr', 'jibun', 'jimok', 'area_m2', 'rings',
         'lat', 'lng',            # 클릭 지점 (재조회용으로 그대로 보관)
         'exact': bool}           # False면 경계 근처라 인접 필지를 골랐다는 뜻

    :raises ParcelLookupError: 조회 자체가 실패한 경우(인증키·API 장애 등)
    """
    feats, meta = VworldClient.fetch_all(_layer_id(), lat, lng, POINT_BUFFER_M)
    if VworldClient.status_of(meta['payload']) == 'ERROR':
        raise ParcelLookupError(VworldClient.error_text(meta['payload'])[:200])
    if not feats:
        return None

    try:
        pt = geo.point_metric(lat, lng)
    except geo.GeoUnavailable as e:
        raise ParcelLookupError('공간연산 라이브러리를 사용할 수 없습니다.') from e

    contained, nearest, nearest_d = None, None, float('inf')
    for f in feats:
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        try:
            gm = geo.to_metric(g)
        except Exception:                                        # noqa: BLE001
            logger.debug('필지 투영 실패', exc_info=True)
            continue
        if gm.contains(pt):
            contained = (f, gm)
            break
        d = float(gm.distance(pt))
        if d < nearest_d:
            nearest, nearest_d = (f, gm), d

    if contained:
        return _as_dict(*contained, lat, lng, exact=True)
    # 경계선을 눌렀을 때만 인접 필지로 대신한다. 멀면 고르지 않는다.
    if nearest and nearest_d <= MAX_SNAP_M:
        return _as_dict(*nearest, lat, lng, exact=False)
    return None


def _as_dict(feat: dict, gm, lat: float, lng: float, *, exact: bool) -> dict:
    p = feat.get('properties') or {}
    return {
        'pnu': p.get('pnu', ''),
        'addr': p.get('addr', ''),
        'jibun': p.get('jibun', ''),
        # 지목은 `"66 도"`처럼 부호 1글자로 붙어 온다 — 정식 명칭으로 편다.
        'jimok': parse_jimok(p.get('jibun', '')) or '미상',
        'jiga': p.get('jiga', ''),
        'area_m2': round(geo.area_m2(gm), 1),
        'rings': geo.rings_4326(gm),
        'lat': round(float(lat), 6),
        'lng': round(float(lng), 6),
        'exact': exact,
    }


def resolve(points: list) -> tuple[list[dict], list[dict]]:
    """
    클릭 좌표 여러 개를 필지로 바꾼다. → (찾은 필지, 못 찾은 좌표)

    같은 필지를 두 번 찍으면 하나로 합친다 — 인접 필지를 묶는 조작에서
    흔히 일어나고, 중복을 그대로 두면 면적이 두 배로 잡힌다.

    한 좌표의 조회가 실패해도 나머지를 버리지 않는다. 빠진 필지는 사유와
    함께 돌려줘, 화면이 '조회 안 됨'과 '필지 없음'을 구분해 말하게 한다.
    """
    found: list[dict] = []
    misses: list[dict] = []
    seen: set[str] = set()

    for lat, lng in points[:MAX_PARCELS]:
        try:
            p = lookup(lat, lng)
        except ParcelLookupError as e:
            misses.append({'lat': lat, 'lng': lng,
                           'reason': 'FETCH', 'detail': str(e)})
            continue
        if not p:
            misses.append({'lat': lat, 'lng': lng,
                           'reason': 'NO_PARCEL',
                           'detail': '해당 좌표에 연속지적 필지가 없습니다.'})
            continue
        key = p['pnu'] or f"{p['lat']},{p['lng']}"
        if key in seen:
            continue
        seen.add(key)
        found.append(p)

    return found, misses


def geometry(parcels: list[dict]):
    """필지 목록 → UTM-K 합집합 도형. 인접 필지는 경계가 붙어 하나로 합쳐진다."""
    geoms = []
    for p in parcels:
        for ring in p.get('rings') or []:
            g = geo.polygon_metric([(a, o) for a, o in ring])
            if g is not None:
                geoms.append(g)
    return geo.union(geoms) if geoms else None


def summarize(parcels: list[dict]) -> dict:
    """보고서·화면이 쓰는 필지 요약 — 지목 구성과 합계 면적."""
    by_jimok: dict[str, dict] = {}
    for p in parcels:
        b = by_jimok.setdefault(p['jimok'], {'count': 0, 'area_m2': 0.0})
        b['count'] += 1
        b['area_m2'] += p['area_m2']
    return {
        'count': len(parcels),
        'total_area_m2': round(sum(p['area_m2'] for p in parcels), 1),
        'by_jimok': {k: {'count': v['count'], 'area_m2': round(v['area_m2'], 1)}
                     for k, v in sorted(by_jimok.items(),
                                        key=lambda kv: -kv[1]['area_m2'])},
        'parcels': [{k: v for k, v in p.items() if k != 'rings'} for p in parcels],
        # 경계 근처를 눌러 인접 필지로 대신한 건이 있으면 화면이 알려야 한다.
        'inexact': [p['pnu'] for p in parcels if not p.get('exact', True)],
    }
