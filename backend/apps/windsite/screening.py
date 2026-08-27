"""
필지 스크리닝 — 화면 범위 필지 4등급 채색
---------------------------------------------------------------
기획서 §3.3 「태양광 — 필지 발굴 모드」의 핵심이다.

■ 탐색 방향을 뒤집는다

지금까지는 후보를 먼저 정하고 하나씩 검증했다(역방향). 그러면 시간의
대부분이 **안 되는 땅을 검토하는 데** 쓰인다. 스크리닝은 안 되는 땅을 먼저
지운 지도에서 후보를 찾게 한다(정방향).

■ 질문이 둘이다 — 후보 적성과 규제 제약

규제만으로 칠하면 마을도 '조건부'가 된다. 집터가 조례 이격 범위 안에 있는
것은 사실이지만, 애초에 **후보가 아닌 땅**이라 그 답은 쓸모가 없다. 농촌
평야는 산재한 주택마다 이격 원이 그려져 화면이 통째로 조건부가 되기도 한다.
그건 판정이 틀린 것이 아니라 실제로 그렇지만, 지도가 한 색이면 아무것도
말하지 않는다.

그래서 축을 둘로 나눈다.

    A. 후보 적성   애초에 후보가 될 땅인가 — 건축물·지목·면적
    B. 규제 제약   규제상 가능한가 — 아래 4등급

A에 걸린 필지는 **'대상 아님'**으로 따로 뺀다. '배제'와 다르다. 배제는
규제 때문에 안 되는 것이고, 대상 아님은 애초에 후보가 아닌 것이다.
지우지는 않는다 — 개수를 집계에 내고, 화면에서 되살려 볼 수 있게 한다.

지목 '대'를 일괄 제외하지 않는다. 나대지 태양광이 실제로 있다. 건축물이
필지를 의미 있게 덮고 있을 때만 뺀다.

■ 이분법 지도를 만들지 않는다 (§3.4)

가능/불가 두 색으로 칠하면 보기는 좋지만 **코드가 법령에 없는 금지를
만들어내는** 가장 위험한 산출물이 된다. 그래서 채색도 판정 4단계를 그대로
따른다.

    가능      어느 제약에도 걸리지 않음
    조건부    협의·저감으로 진행 가능한 제약에 걸림
    배제      조문이 직접 금지하는 제약에 걸림
    미확인    **조회하지 못했다.** 제약이 없다는 뜻이 아니다

조례가 등록되지 않은 지자체의 필지는 절대 '가능'으로 칠하지 않는다.
"조회 안 됨"과 "제약 없음"은 전혀 다른 사실이다.

■ 성능 — 레이어를 필지마다 조회하지 않는다

필지 300개에 레이어 50종이면 15,000번 조회다. 화면 범위로 **한 번만** 받아
합집합을 만들어 두고, 필지는 그 도형과의 교차만 본다. 교차 판정은 shapely
prepared 도형으로 필지당 수십 마이크로초다.

■ 고줌에서만 칠한다

연속지적은 전국 약 3,900만 필지이고 V-World 폴리곤 요청은 10km²가 상한이다.
화면이 그보다 넓으면 칠하지 않고 **왜 안 칠하는지** 돌려준다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from . import (available, energy as energy_mod, geo, jurisdiction, ordinances,
               roads)
from .providers.cadastral import EXCLUDED as PUBLIC_JIMOK, parse_jimok  # noqa: F401
from .providers.vworld import VworldClient
from .schemas import Status

logger = logging.getLogger(__name__)

#: 채색 상한 면적(m²).
#:
#: 종전에 10km²로 두었던 것은 **V-World 1회 요청 한도를 시스템 한도로 착각한**
#: 값이었다. `VworldClient.fetch_area`가 이미 타일로 나눠 받으므로 조회는
#: 576km²(타일 64장)까지 된다.
#:
#: 실제 병목은 조회가 아니라 아래 셋이다(2026-08 실측, 홍성 농촌 656필지/km²).
#:   · 필지 수    3,000개에서 이미 4.6km²에 걸렸다 — 면적보다 먼저 막힌다
#:   · 응답 크기  3,000필지에 1.66MB. 필지 수에 비례한다
#:   · 렌더링     폴리곤 수만 개면 브라우저가 멈춘다
#:
#: 그래서 면적만 키우지 않고 셋을 함께 손봤다. 도시처럼 필지가 촘촘한 곳은
#: 이 면적에서도 필지 수 상한에 먼저 걸리며, 그 사실은 truncated로 알린다.
MAX_SCREEN_AREA_M2 = 50_000_000

#: 한 화면에 칠할 최대 필지 수. 넘으면 잘라내고 그 사실을 함께 돌려준다 —
#: 조용히 자르면 '이 화면엔 필지가 이만큼뿐'으로 읽힌다.
MAX_PARCELS = 8000

#: 후보가 아닌 필지(대상 아님·배제)의 **도형을 응답에서 뺄지**.
#: 화면 기본값이 '후보만 보기'라 어차피 그리지 않는 도형인데, 실측상
#: 좌표의 54%를 차지한다. 개수와 사유는 그대로 보내므로 집계는 정확하고,
#: 사용자가 '후보만 보기'를 끄면 도형까지 다시 받는다.
SHAPES_CANDIDATES = 'candidates'
SHAPES_ALL = 'all'

#: 레이어 동시 조회 수
MAX_WORKERS = 6

#: 등급 — 앞의 넷은 규제 축, NOT_APPLICABLE은 후보 적성 축이다.
GRADE_POSSIBLE = 'POSSIBLE'
GRADE_CONDITIONAL = 'CONDITIONAL'
GRADE_BLOCKED = 'IMPOSSIBLE'
GRADE_UNKNOWN = 'UNKNOWN'
GRADE_NA = 'NOT_APPLICABLE'

#: 사업 부지로 성립하는 최소 면적(m²). 100kW급도 1,300m² 안팎이 필요하고,
#: 연속지적에는 15~30m² 자투리가 대량으로 섞여 있어 이걸 걸러야 지도가 읽힌다.
#: 인접 필지를 묶어 쓰는 경우가 많아 너무 높이면 후보를 놓친다.
DEFAULT_MIN_AREA_M2 = 1000

#: 건축물이 필지의 이 비율 이상을 덮으면 후보에서 뺀다.
#: 20,000m² 농지 위의 농막 100m²(0.5%)는 통과시키고, 800m² 집터의 주택
#: 120m²(15%)는 걸러내기 위한 값이다. 건물이 있다는 사실만으로 빼면
#: 농막 하나 때문에 멀쩡한 농지가 후보에서 사라진다.
BUILDING_COVER_RATIO = 0.10

#: 발전부지가 될 수 없는 지목.
#: 공공용지(도로·하천·구거…)와 이미 다른 용도로 확정된 땅(학교·종교·묘지…)이다.
#: '대'는 넣지 않는다 — 나대지가 있어 건축물 유무로 가른다.
NOT_SITE_JIMOK = set(PUBLIC_JIMOK) | {
    '학교용지', '종교용지', '묘지', '주차장', '주유소용지', '체육용지',
    '유원지', '사적지', '광천지', '공원',
}


class ScreenTooWide(RuntimeError):
    """화면이 넓어 필지를 칠할 수 없다."""


def screen(bounds: tuple, energy: str = energy_mod.DEFAULT,
           min_area_m2: int = DEFAULT_MIN_AREA_M2,
           shapes: str = SHAPES_CANDIDATES) -> dict:
    """화면 범위(bounds = (남, 서, 북, 동), EPSG:4326)를 사각형으로 보고 훑는다."""
    south, west, north, east = bounds
    area = geo.polygon_metric([(south, west), (south, east),
                               (north, east), (north, west)])
    if area is None:
        raise ValueError('화면 범위가 유효하지 않습니다.')
    return screen_area(area, energy, min_area_m2, shapes)


def screen_area(area, energy: str = energy_mod.DEFAULT,
                min_area_m2: int = DEFAULT_MIN_AREA_M2,
                shapes: str = SHAPES_CANDIDATES,
                grandfathered: bool = False) -> dict:
    """
    **사용자가 그린 사업구역** 안의 필지를 4등급으로 나눈다.

    화면 전체를 훑는 것보다 이쪽이 낫다. 실측(홍성 농촌 656필지/km²)에서
    4.6km² 화면은 필지 3,459개였고 그중 55%가 '대상 아님'이었다 — 관심 밖
    필지를 절반 넘게 조회한 셈이다. 10ha 구역이면 필지 66개로 1/52다.

    구역 제약도(`available.compute`)가 규제 레이어를 **면적**으로 칠한다면,
    이쪽은 같은 구역을 **필지 경계**로 가른다. 제약도만으로는 '이 필지가
    되는가'를 알 수 없어 둘을 함께 낸다.

    반환::

        {'parcels': [{pnu, jibun, jimok, grade, reasons, rings}, …],
         'counts': {등급: 수},
         'area_km2', 'truncated', 'fetch_failures',
         'jurisdictions': [{sigungu, ordinance_state}],
         'unverified': [시군구…]}
    """
    if area is None:
        raise ValueError('검토 구역이 유효하지 않습니다.')
    if area.area > MAX_SCREEN_AREA_M2:
        raise ScreenTooWide(
            f'화면 범위가 {area.area / 1e6:,.1f}km²로 상한'
            f'({MAX_SCREEN_AREA_M2 / 1e6:.0f}km²)을 넘습니다. 더 확대하십시오.')

    prof = energy_mod.profile(energy)

    # ── 1. 관할 지자체와 조례 ────────────────────────────────────────
    try:
        slices, jmeta = jurisdiction.with_ordinances(area, prof.code)
    except jurisdiction.BoundaryUnavailable as e:
        raise ScreenTooWide(f'행정경계를 조회하지 못했습니다: {e}') from e

    # 조례를 확인하지 못한 지자체. 그 관할 필지는 '가능'으로 칠하지 않는다.
    unverified = {s['sigungu'] for s in slices
                  if s['ordinance_state'] not in (ordinances.HAS_RULES,
                                                  ordinances.NO_RULE)}
    unverified_geom = geo.union([s['geom'] for s in slices
                                 if s['sigungu'] in unverified]) if unverified else None

    # ── 2. 제약 레이어 (화면당 1회) ──────────────────────────────────
    # 합치지 않고 레이어별로 들고 있는다. 필지를 눌렀을 때 **무엇 때문에**
    # 조건부인지 말하려면 이름이 남아 있어야 한다.
    layers, failures = _constraint_layers(area, prof.facility_height_m)

    # ── 3. 조례 이격 버퍼 ────────────────────────────────────────────
    # 주거 이격과 그 밖의 이격을 **나눠** 받는다. 주거는 조문이 직접 금지하는
    # 범위라 배제로, 축사·정온시설은 조례마다 대상 범위가 달라 조건부로 센다.
    # 합쳐 쓰면 구역 검토(available)와 필지 채색이 어긋나 같은 땅이 문서에서는
    # 배제로, 지도에서는 조건부로 나온다.
    try:
        buffers, buf_notes = available._facility_buffers(area, slices)  # noqa: SLF001
        ord_house, ord_other = available.ordinance_zones(buffers)
    except Exception:                                           # noqa: BLE001
        logger.exception('조례 이격 버퍼 생성 실패')
        ord_house = ord_other = None
        buf_notes = ['조례 이격 버퍼를 만들지 못했습니다.']

    # 도로 이격도 함께 본다. 이것이 빠져 있으면 **제약도에서는 붉게 칠해진
    # 자리인데 그 아래 필지는 조건부**로 나온다 — 같은 땅을 두고 지도와
    # 필지 채색이 서로 다른 말을 하게 된다(장흥 실측: 배제 필지 0개).
    try:
        rule_sets = {s['code']: available._rule_set(s['rules'])  # noqa: SLF001
                     for s in slices if s.get('rules')}
        road_zones, _rn, _rl, _rd = roads.setback_zones(area, rule_sets, slices)
    except Exception:                                           # noqa: BLE001
        logger.exception('도로 이격 버퍼 생성 실패')
        road_zones = {}
    road_block = road_zones.get('blocked')
    road_unc = road_zones.get('uncertain')

    # 경과규정 대상이면 조례 이격을 배제로 칠하지 않는다. 구역 검토가 이미
    # 조건부로 셌는데 필지만 붉게 칠하면, 같은 땅을 두고 지도와 필지가
    # 서로 다른 말을 한다.
    if grandfathered:
        ord_other = geo.union([g for g in (ord_other, ord_house, road_block)
                               if g is not None])
        ord_house = road_block = None

    # ── 4. 필지 ──────────────────────────────────────────────────────
    # ⚠️ fetch_area는 도형의 **외접원**으로 조회하고 구역 밖 피처까지 돌려준다
    #    (문서화된 동작이며, 자르는 판단을 호출자에게 맡긴다). 규제 레이어는
    #    `_layer_geoms`가 구역으로 잘라 오지만 필지는 그렇지 않다.
    #
    #    자르지 않으면 **구역 밖 필지가 전부 '가능'으로 칠해진다.** 제약이
    #    없어서가 아니라 그 바깥에서는 아무 레이어도 조회하지 않았기 때문이다
    #    (레이어·조례 버퍼가 모두 구역 안으로 잘려 있다). 조회하지 않은 것을
    #    '가능'으로 칠하는 것은 이 시스템이 가장 경계하는 실패다(§3.4).
    feats, meta = VworldClient.fetch_area(_parcel_layer_id(), area)
    if meta.get('strategy') == 'failed':
        raise ScreenTooWide('연속지적을 조회하지 못했습니다: '
                            + str(meta.get('error') or ''))
    area_prep = _prep_one(area)

    # ── 5. 건축물 — 후보 적성 판정에 쓴다 ────────────────────────────
    bldgs = _buildings(area)

    prep = _prepare({'ordinance_house': ord_house, 'ordinance_other': ord_other,
                     'road_block': road_block, 'road_unc': road_unc,
                     'unverified': unverified_geom,
                     'buildings': geo.union(bldgs) if bldgs else None})
    prep_layers = [{**l, 'prep': _prep_one(l['geom'])} for l in layers]

    # 면적 미달로만 걸리는 필지 중 이미 가용한 이웃과 맞닿은 것을 미리
    # 가려 둔다 — site_geom()과 반드시 같은 규칙이어야 사업구역 도형과
    # 화면 채색이 어긋나지 않는다(_na_reason 문서 참고).
    rescued_pnus = _rescue_undersized_pnus(feats, area, area_prep,
                                           prep.get('buildings'), min_area_m2)

    out: list[dict] = []
    counts = {GRADE_POSSIBLE: 0, GRADE_CONDITIONAL: 0, GRADE_BLOCKED: 0,
              GRADE_UNKNOWN: 0, GRADE_NA: 0}
    truncated = bool(meta.get('truncated'))

    outside = 0
    for f in feats:
        if len(out) >= MAX_PARCELS:
            truncated = True
            break
        row = _grade(f, prep, prep_layers, bool(failures), min_area_m2,
                     area, area_prep, rescued_pnus)
        if row is None:
            outside += 1
            continue
        counts[row['grade']] += 1
        # 후보가 아닌 필지는 도형을 뺀다. 개수·지번·사유는 그대로 남으므로
        # 집계와 설명은 정확하고, 화면이 '후보만 보기'를 끄면 다시 받는다.
        if shapes == SHAPES_CANDIDATES and row['grade'] in (GRADE_NA, GRADE_BLOCKED):
            row['rings'] = []
        out.append(row)

    return {
        'parcels': out,
        'counts': counts,
        'area_km2': round(area.area / 1e6, 2),
        'min_area_m2': min_area_m2,
        'shapes': shapes,
        'building_count': len(bldgs),
        # 외접원에 딸려 왔으나 구역 밖이라 판정하지 않은 필지 수.
        # 숨기지 않고 낸다 — '왜 화면의 저 필지는 색이 없나'에 답이 된다.
        'outside_count': outside,
        'truncated': truncated,
        'fetch_failures': failures,
        'notes': buf_notes,
        'jurisdictions': [{'sido': s['sido'], 'sigungu': s['sigungu'],
                           'ordinance_state': s['ordinance_state'],
                           'rule_count': len(s['rules'])} for s in slices],
        'unverified': sorted(unverified),
        'energy_type': prof.code,
    }


# ----------------------------------------------------------------------
def _parcel_layer_id() -> str:
    from .models import RegulationLayer
    lyr = RegulationLayer.objects.filter(code='연속지적', is_active=True).first()
    return lyr.layer_id if lyr else 'lp_pa_cbnd_bubun'


def _constraint_layers(area, facility_height_m: float = 200.0):
    """
    화면 범위의 제약 레이어를 한 번에 받아 **레이어별로** 돌려준다.

    합집합으로 뭉치지 않는 이유는 하나다 — 필지를 눌렀을 때 '무엇 때문에
    조건부인지'를 말해야 하기 때문이다. 조례 이격에 걸린 것과 농업진흥지역에
    걸린 것은 사업자에게 전혀 다른 이야기다(앞은 협의 여지, 뒤는 전용 절차).
    등급 색은 하나로 두되 사유는 남긴다.

    항공 공역은 **하한고도 이상**에만 적용되므로, 설비 최고높이보다 높은 곳에
    걸린 피처는 빼고 본다. 태양광 모듈(5m)이 접근관제구역·경계구역에 걸려
    조건부로 뜨던 오판을 여기서 막는다. 지표를 포함하는 공역(비행금지구역 등)은
    그대로 남는다 — 높이와 무관하게 적용되기 때문이다.

    구역 검토(`available._compute`)와 같은 규칙으로 가른다 — 용도지역은 전
    국토를 덮는 분류라 뺀다(ZONING_LAYER_IDS).

    ⚠️ **화면을 통째로 덮는 레이어라도 여기서는 빼지 않는다.** `available.
    _compute`는 한때 그렇게 했다가 사고가 났다(2026-08, 장흥·완도 실측) —
    농업진흥지역도가 사업구역의 100.00%를 덮어 blanket으로 빠지자 그 전체가
    조건부가 아니라 "제약없음"으로 계산됐다. 그 사고 이후 `available._compute`
    는 blanket 레이어도 blocked/conditional 합계에는 반드시 넣도록 고쳤다
    (`BLANKET_RATIO`는 이제 항목별 **표**에서만 뺀다).

    이쪽(필지 채색)은 애초에 "표"가 없이 필지마다 걸린 사유를 그대로 돌려주는
    구조라, 화면 범위 기준 blanket 비율로 레이어를 통째로 빼면 **그 사유
    자체가 필지의 reasons에서 사라진다** — 다른(무관한) 사유가 겹쳐 같은
    조건부 색으로 칠해지고 있으면, 사용자는 진짜 사유(농업진흥지역)를 못 보고
    엉뚱한 사유(조례 도로 이격 등)만 보게 된다. 화면 뷰포트는 사용자가 얼마나
    확대했는지에 따라 우연히 바뀌는 값이라 "이 레이어가 실제로 전 국토급인가"
    를 가르는 기준이 되지 못한다 — 그래서 여기서는 애초에 이 필터를 두지
    않는다.
    """
    from .models import RegulationLayer, RegulationRule

    layers = list(RegulationLayer.objects
                  .filter(is_active=True, provider='VWORLD')
                  .exclude(role__in=available.NON_CONSTRAINT_ROLES))
    overrides = {(r.layer, r.condition_key): r.status
                 for r in RegulationRule.objects.filter(is_active=True)}

    out, failures = [], []

    def work(layer):
        if layer.altitude_floor_field:
            return layer, _airspace_geoms(area, layer, facility_height_m)
        return layer, available._layer_geoms(area, layer)        # noqa: SLF001

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for layer, (geoms, err) in pool.map(work, layers):
            if err:
                failures.append(f'{layer.title}: {err}')
            if not geoms or layer.layer_id in available.ZONING_LAYER_IDS:
                continue
            merged = geo.union(geoms)
            if merged is None:
                continue
            status = overrides.get((layer.title, ''), layer.default_status)
            if status == Status.POSSIBLE.value:
                continue
            out.append({'title': layer.title, 'status': status, 'geom': merged})

    return out, failures


def _airspace_geoms(area, layer, facility_height_m: float) -> tuple[list, str]:
    """
    공역 레이어 — 설비 최고높이보다 **하한고도가 낮은** 피처만 남긴다.

    `_layer_geoms`는 도형만 돌려주므로 고도를 볼 수 없다. 여기서는 속성까지
    읽어 하한고도를 판별한다. 하한을 읽지 못한 피처는 남긴다 — 모르는 것을
    '저촉 아님'으로 지우면 조용히 제약을 놓친다.
    """
    from .providers.vworld import parse_altitude_ft

    _FT_PER_M = 3.28084
    tip_ft = float(facility_height_m) * _FT_PER_M
    try:
        feats, meta = VworldClient.fetch_area(layer.layer_id, area)
    except Exception as e:                                      # noqa: BLE001
        return [], f'{type(e).__name__}'
    if meta.get('strategy') == 'failed':
        return [], meta.get('error') or '조회 실패'

    out = []
    for f in feats:
        floor = parse_altitude_ft((f.get('properties') or {}).get(
            layer.altitude_floor_field))
        # 하한이 설비 높이보다 높으면 평면이 겹쳐도 저촉이 아니다.
        if floor is not None and floor > tip_ft:
            continue
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        clipped = geo.clip(geo.to_metric(g), area)
        if clipped is not None:
            out.append(clipped)
    return out, ('구역 일부만 조회됨' if meta.get('truncated') else '')


def _buildings(area) -> list:
    """
    화면 범위의 건축물 도형.

    마을 필지를 후보에서 빼는 **가장 정확한 신호**다. 지목만 보면 '대'에
    나대지가 섞이고, 마을이라도 지목이 전·답인 텃밭이 끼어 있다. 건물이
    실제로 필지를 덮고 있는지가 답에 가깝다.

    조례 이격 버퍼가 같은 레이어를 이미 받으므로 캐시가 살아 있어 사실상
    추가 비용이 없다.
    """
    try:
        feats, meta = VworldClient.fetch_area(available.FACILITY_LAYER, area)
    except Exception:                                       # noqa: BLE001
        logger.warning('건축물 조회 실패 — 후보 적성 판정에서 제외', exc_info=True)
        return []
    if meta.get('strategy') == 'failed':
        return []
    out = []
    for f in feats:
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        try:
            out.append(geo.to_metric(g))
        except Exception:                                   # noqa: BLE001
            continue
    return out


def _prepare(geoms: dict) -> dict:
    """교차 판정을 빠르게 하려고 prepared 도형으로 바꿔 둔다."""
    try:
        from shapely.prepared import prep
    except ImportError:                                         # pragma: no cover
        return {k: {'geom': v, 'prep': None} for k, v in geoms.items()}
    out = {}
    for k, g in geoms.items():
        out[k] = {'geom': g, 'prep': prep(g) if g is not None else None}
    return out


def _prep_one(g):
    try:
        from shapely.prepared import prep
        return prep(g) if g is not None else None
    except ImportError:                                     # pragma: no cover
        return None


def _hits(prep_entry, gm) -> bool:
    if not prep_entry or prep_entry['geom'] is None:
        return False
    p = prep_entry['prep']
    return bool(p.intersects(gm)) if p else bool(prep_entry['geom'].intersects(gm))


def _na_reason(jimok: str, area_m2: float, min_area_m2: int,
               building_cover: float = 0.0, rescued: bool = False) -> str | None:
    """
    필지가 **후보(사업 대상)가 될 수 없는** 이유. 후보면 None.

    이 판정은 두 곳이 함께 쓴다 — 필지 채색(`_grade`)과 사업구역 도형
    (`site_geom`). 한 곳에서만 규칙을 바꾸면 화면에서 무색인 필지가
    사업구역에는 들어 있는(또는 그 반대) 불일치가 생기므로, **반드시 이
    함수 하나로만** 가른다.

    `rescued=True`면 면적 미달만은 실격 사유로 보지 않는다(지목·건축물
    사유는 그대로 적용). 최소면적 기준은 15~30㎡짜리 자투리를 걸러내려는
    것인데, 이미 가용한 이웃 필지와 맞닿아 실제로는 하나의 영농 단위로
    쓰이는 981㎡짜리 땅까지 잘라내는 것은 그 취지와 다르다(실측: 장흥
    덕산리 1139-7 — 최소기준 1,000㎡에 18㎡ 모자라지만 사방이 후보 필지).
    누가 이웃한 가용 필지인지는 이 함수가 알 수 없으므로, 호출부(`_rescue_
    undersized`)가 인접 관계를 먼저 가리고 그 결과만 넘긴다.
    """
    if area_m2 < min_area_m2 and not rescued:
        return f'면적 {area_m2:,.0f}㎡ — 최소 {min_area_m2:,}㎡ 미만'
    if jimok in NOT_SITE_JIMOK:
        return f'{jimok} — 발전부지가 될 수 없는 지목'
    # 건축물이 필지를 의미 있게 덮고 있으면 후보가 아니다(마을·축사·공장).
    if building_cover >= BUILDING_COVER_RATIO:
        return f'건축물이 필지의 {building_cover * 100:.0f}%를 차지 — 기존 건물 부지'
    return None


#: 인접 판정 허용 오차(m) — 필지 경계 좌표의 미세한 정밀도 오차를 흡수한다.
ADJACENT_RESCUE_M = 0.5


def _rescue_undersized(usable: list, maybe: list[dict]) -> set:
    """
    면적 미달로만 걸린 필지(`maybe`) 중 **이미 가용한 이웃**(`usable`)과
    맞닿은 것을 구제한다.

    `usable` = 이미 후보로 확정된 필지 도형 목록(shapely, EPSG:5179).
    `maybe`  = [{'pnu', 'gm', 'jimok', 'area_m2', 'cover'}, …] — 호출부가
    `_na_reason(..., rescued=True) is None`으로 "면적만 모자람"을 미리
    가려낸 목록이어야 한다. 사슬로 이어진 경우(작은 필지 여럿이 이어져
    결국 정상 필지에 닿는 경우)도 구제되도록, 구제될 때마다 그 도형을
    가용 목록에 더해 고정점까지 반복한다.

    → 구제된 필지의 pnu 집합.
    """
    usable = list(usable)
    rescued: set = set()
    changed = True
    while changed and maybe:
        changed = False
        still = []
        for p in maybe:
            if any(p['gm'].distance(u) <= ADJACENT_RESCUE_M for u in usable):
                usable.append(p['gm'])
                rescued.add(p['pnu'])
                changed = True
            else:
                still.append(p)
        maybe = still
    return rescued


def _rescue_undersized_pnus(feats: list[dict], area, area_prep, bldg_prep,
                            min_area_m2: int) -> frozenset:
    """
    `screen_area()`용 — 원본 필지 목록(feats)에서 곧바로 구제 대상 pnu를
    가려낸다. `_grade()`가 필지를 하나씩 판정하므로(이웃을 모를 수밖에
    없다), 채색 루프를 돌기 전에 이 함수로 인접 관계를 먼저 계산해 둔다.
    """
    usable, maybe = [], []
    for f in feats:
        props = f.get('properties') or {}
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        try:
            gm = geo.to_metric(g)
        except Exception:                                       # noqa: BLE001
            continue
        if not _hits({'geom': area, 'prep': area_prep}, gm):
            continue
        jimok = parse_jimok(props.get('jibun', '')) or '미상'
        area_m2 = geo.area_m2(gm)
        cover = _building_cover(bldg_prep, gm)
        if _na_reason(jimok, area_m2, min_area_m2, cover) is None:
            usable.append(gm)
        elif _na_reason(jimok, area_m2, min_area_m2, cover, rescued=True) is None:
            maybe.append({'pnu': props.get('pnu', ''), 'gm': gm,
                         'jimok': jimok, 'area_m2': area_m2, 'cover': cover})
    return frozenset(_rescue_undersized(usable, maybe))


#: 물길 성격의 지목. 표시용 외곽이 어떤 폭에서도 덮으면 안 된다 —
#: 하천이 사업구역 안에 든 것처럼 그려지는 순간, 인허가 도면으로서 틀린
#: 그림이 된다(3차 실측: 폭 20m 이하 구거가 닫혀 수계 10.4ha가 덮였다).
WATER_JIMOK = ('하천', '구거', '제방', '유지')


def site_geom(drawn, energy: str = energy_mod.DEFAULT,
              min_area_m2: int = DEFAULT_MIN_AREA_M2) -> tuple:
    """
    사용자가 그린 폴리곤 → **사업구역 도형**(사업 대상 필지들의 합 ∩ 그린 구역).

    ⚠️ 왜 그린 폴리곤을 그대로 쓰면 안 되는가 (장흥 실측, 2026-08)

       그린 폴리곤(216.9ha)은 서쪽 물길 — 지적상 구거 75·도로 109·제방 7
       필지, 합 26.5ha — 를 안에 품고 있었다. 화면 채색은 '대상 아님'
       필지를 칠하지 않으므로 눈에는 물길이 구역 밖으로 보였지만, 판정은
       그린 폴리곤 전체로 돌았다. 그래서 물길·수변에 지정된 생태자연도
       2등급이 '사업구역 내 2등급'으로 잡혔다 — 지도가 말하는 구역과
       판정이 본 구역이 달랐던 것이다.

       인허가 실무의 사업구역은 **편입 필지들의 합**이다. 그린 폴리곤은
       필지를 고르는 올가미일 뿐이므로, 판정·면적·지도가 쓰는 단일
       진실원천은 이 함수가 낸 도형이다.

    후보 적성 규칙은 필지 채색과 **같은 함수**(`_na_reason`)를 쓴다.
    건축물 조회가 실패하면 그 축은 관대하게(cover=0) 본다 — 채색도 같은
    상황에서 같은 동작이다.

    → (site_geom, water_geom, meta)
       water_geom — 그린 구역 안 **수계 필지**(하천·구거·제방·유지)의 합.
       표시용 외곽(site_outline)이 좁은 틈을 닫을 때 물길까지 메우지
       않도록, 닫은 뒤 이 도형을 도로 뺀다. 없으면 None.
       meta = {'parcel_count', 'na_count', 'na_m2', 'drawn_m2', 'site_m2',
               'fallback': 왜 그린 폴리곤으로 되돌아갔는가 (성공 시 없음)}
    """
    drawn_m2 = float(drawn.area)

    def fallback(why: str) -> tuple:
        logger.warning('사업구역 필지 정제 실패 — 그린 폴리곤을 그대로 씁니다: %s', why)
        return drawn, None, {'drawn_m2': drawn_m2, 'site_m2': drawn_m2,
                             'fallback': why}

    try:
        feats, meta = VworldClient.fetch_area(_parcel_layer_id(), drawn)
    except Exception as e:                                      # noqa: BLE001
        return fallback(f'연속지적 조회 실패: {type(e).__name__}')
    if meta.get('strategy') == 'failed':
        return fallback('연속지적 조회 실패: ' + str(meta.get('error') or ''))

    try:
        bldgs = _buildings(drawn)
    except Exception:                                           # noqa: BLE001
        logger.exception('사업구역 정제용 건축물 조회 실패 — 건물 축은 무시')
        bldgs = []
    bldg_union = geo.union(bldgs) if bldgs else None
    bldg_prep = _prep_one(bldg_union) if bldg_union is not None else None
    drawn_prep = _prep_one(drawn)

    keep, water, n_all, n_na, na_m2 = [], [], 0, 0, 0.0
    maybe: list[dict] = []          # 면적만 모자란 필지 — 인접 구제 후보
    for f in feats:
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        try:
            gm = geo.to_metric(g)
        except Exception:                                       # noqa: BLE001
            continue
        if not _hits({'geom': drawn, 'prep': drawn_prep}, gm):
            continue                                   # 그린 구역 밖
        n_all += 1
        props = f.get('properties') or {}
        jimok = parse_jimok(props.get('jibun', '')) or '미상'
        area_m2 = geo.area_m2(gm)
        cover = _building_cover({'geom': bldg_union, 'prep': bldg_prep}
                                if bldg_union is not None else None, gm)
        if _na_reason(jimok, area_m2, min_area_m2, cover) is None:
            keep.append(gm)
        elif _na_reason(jimok, area_m2, min_area_m2, cover, rescued=True) is None:
            maybe.append({'pnu': props.get('pnu', ''), 'gm': gm,
                         'jimok': jimok, 'area_m2': area_m2, 'cover': cover})
        else:
            n_na += 1
            na_m2 += float(gm.intersection(drawn).area)
            if jimok in WATER_JIMOK:
                water.append(gm)

    rescued = _rescue_undersized(keep, maybe)
    for m in maybe:
        if m['pnu'] in rescued:
            keep.append(m['gm'])
        else:
            n_na += 1
            na_m2 += float(m['gm'].intersection(drawn).area)
            if m['jimok'] in WATER_JIMOK:
                water.append(m['gm'])

    if not keep:
        return fallback('그린 구역 안에서 사업 대상 필지를 찾지 못했습니다')

    site = geo.union(keep)
    site = site.intersection(drawn)         # 경계 걸친 필지는 구역 안 몫만
    if site is None or site.is_empty:
        return fallback('대상 필지와 그린 구역의 교집합이 비어 있습니다')

    return site, geo.union(water), {
        'parcel_count': n_all, 'na_count': n_na,
        'na_m2': round(na_m2, 1),
        'drawn_m2': round(drawn_m2, 1),
        'site_m2': round(float(site.area), 1)}


def _grade(feat: dict, prep: dict, layers: list, had_failure: bool,
           min_area_m2: int, area=None, area_prep=None,
           rescued_pnus: frozenset = frozenset()) -> dict | None:
    """
    필지 하나의 등급. **검토 구역 밖이면 None** — 판정하지 않는다.

    **후보 적성을 먼저 본다.** 규제 판정은 후보가 될 땅에서만 뜻이 서기
    때문이다 — 집터가 조례 이격 범위 안이라는 답은 맞지만 쓸모가 없다.

    규제 축에서는 **가장 나쁜 것이 이긴다.** 한 귀퉁이만 걸려도 그 제약은
    해결해야 하므로 면적 비율로 눅이지 않는다. 다만 걸린 항목을 **모두**
    모아 돌려준다 — 등급 색은 하나여도, 필지를 눌렀을 때 조례 이격 때문인지
    농업진흥지역 때문인지 알 수 있어야 다음 행동이 갈린다.
    """
    props = feat.get('properties') or {}
    g = geo.geom_from_geojson(feat.get('geometry'))
    if g is None:
        return None
    try:
        gm = geo.to_metric(g)
    except Exception:                                           # noqa: BLE001
        return None

    # 검토 구역 밖 필지는 판정하지 않는다. 그 바깥에서는 규제 레이어도
    # 조례 버퍼도 조회하지 않았으므로, 칠하면 '안 본 것'을 '제약 없음'으로
    # 말하는 셈이 된다.
    inside_ratio = 1.0
    if area is not None:
        if not _hits({'geom': area, 'prep': area_prep}, gm):
            return None
        try:
            inter = area.intersection(gm)
            inside_ratio = float(getattr(inter, 'area', 0.0)) / (float(gm.area) or 1.0)
        except Exception:                                       # noqa: BLE001
            inside_ratio = 1.0

    jimok = parse_jimok(props.get('jibun', '')) or '미상'
    area_m2 = geo.area_m2(gm)

    def row(grade, reasons):
        rep = geo.representative_latlng(gm)
        # 구역 경계에 걸친 필지는 **구역 안 부분만** 판정한 것이다.
        # 그 사실을 적지 않으면 필지 전체가 그 등급인 줄로 읽힌다.
        if inside_ratio < 0.995:
            reasons = [*reasons,
                       f'구역 경계에 걸침 — 구역 안 {inside_ratio * 100:.0f}%만 판정']
        return {
            'pnu': props.get('pnu', ''),
            'jibun': props.get('jibun', ''),
            'jimok': jimok,
            'area_m2': round(area_m2, 1),
            'grade': grade,
            'reasons': reasons,
            # 소수점 5자리 ≈ 1m. 채색 표시용이라 이 이상은 페이로드만 늘린다
            # (정밀판정은 서버가 원본 지적을 다시 받아 쓰므로 영향이 없다).
            'rings': geo.rings_4326(gm, precision=5),
            'lat': round(rep[0], 6) if rep else None,
            'lng': round(rep[1], 6) if rep else None,
            'inside_ratio': round(inside_ratio, 3),
        }

    # ── A. 후보 적성 ────────────────────────────────────────────────
    # 면적 미달로만 걸리는 필지가 이미 가용한 이웃과 맞닿아 있으면 사전에
    # 구제된다(screen_area의 _rescue_undersized 참고) — site_geom()과
    # 같은 규칙이라야 화면 채색과 사업구역 도형이 어긋나지 않는다.
    na = _na_reason(jimok, area_m2, min_area_m2,
                    _building_cover(prep.get('buildings'), gm),
                    rescued=props.get('pnu', '') in rescued_pnus)
    if na:
        return row(GRADE_NA, [na])

    # ── B. 규제 제약 ────────────────────────────────────────────────
    blocked, conditional = [], []
    for l in layers:
        if not _hits({'geom': l['geom'], 'prep': l['prep']}, gm):
            continue
        (blocked if l['status'] == Status.IMPOSSIBLE.value
         else conditional).append(_annotate(l['title']))

    # 조례 주거 이격은 **조문이 직접 금지하는 범위**다. 대상 주택을 건축물대장
    # 주용도로 확인하므로 창고까지 배제하던 과대 배제가 없다. 구역 검토
    # (available.compute)와 같은 기준이라 문서와 지도가 어긋나지 않는다.
    if _hits(prep.get('ordinance_house'), gm):
        blocked.insert(0, '지자체 조례 주거 이격거리 — 사업 불가')
    # 도로 이격 — 등급이 분명한 국도·지방도는 배제, 시·군도는 조건부다.
    # 구역 검토(available.compute)와 같은 기준이라 지도와 필지가 어긋나지 않는다.
    if _hits(prep.get('road_block'), gm):
        blocked.insert(0, '지자체 조례 도로 이격거리 (국도·지방도) — 사업 불가')
    if _hits(prep.get('road_unc'), gm):
        conditional.insert(0, '지자체 조례 도로 이격 (시·군도 — 군도 여부 확인 필요)')
    # 축사·정온시설 이격은 조례마다 대상 범위가 달라 조건부로 남긴다.
    if _hits(prep.get('ordinance_other'), gm):
        conditional.insert(0, '지자체 조례 축사·정온시설 이격 범위')

    if blocked:
        return row(GRADE_BLOCKED, blocked)
    if conditional:
        return row(GRADE_CONDITIONAL, conditional)
    if _hits(prep.get('unverified'), gm):
        # 조례를 확인하지 못한 지자체 — 절대 '가능'으로 칠하지 않는다(§3.4)
        return row(GRADE_UNKNOWN, ['조례 미확인 지자체 — 이격 제약을 보지 못함'])
    if had_failure:
        return row(GRADE_UNKNOWN, ['일부 레이어 조회 실패 — 보지 못한 제약 가능'])
    return row(GRADE_POSSIBLE, [])


def _building_cover(prep_entry, gm) -> float:
    """필지 면적 대비 건축물이 덮은 비율. 건물이 없으면 0."""
    if not prep_entry or prep_entry['geom'] is None:
        return 0.0
    if not _hits(prep_entry, gm):
        return 0.0
    try:
        inter = prep_entry['geom'].intersection(gm)
        a = float(getattr(inter, 'area', 0.0))
    except Exception:                                           # noqa: BLE001
        return 0.0
    total = float(gm.area) or 1.0
    return a / total


def _annotate(title: str) -> str:
    """
    레이어 이름에 부연을 붙인다. 필지를 눌렀을 때 **다음 행동**이 서야 한다.

    '농업진흥지역도'만 뜨면 불가로 읽힌다. 당사 사업 경로는 전용이 아니라
    염도평가 → 농지 타용도 일시사용허가이므로, 그 사실이 지도에서 바로
    보여야 후보를 버리지 않는다(구역 검토의 겹침 문구와 같은 규칙이다).
    """
    from .available import OVERLAY_ANNOTATION
    from .providers.solar_site import SALINE_DSM

    note = next((t for key, t in OVERLAY_ANNOTATION if key in title), '')
    return f'{title} ({note.format(saline=SALINE_DSM)})' if note else title
