"""
사업구역 제약도 · 가용면적 산출
---------------------------------------------------------------
점 검토는 "이 지점이 규제에 걸리는가"에 답한다. 구역 검토는 그 질문이
성립하지 않는다. 수천 ha 구역이면 어딘가는 반드시 걸리기 때문이다.
구역에서 알고 싶은 것은 **어디가 얼마나 걸리고, 쓸 수 있는 땅이 얼마나
남는가**다.

■ 왜 '배제/가용' 이분법으로 만들지 않는가

이 시스템의 판정 기준(RegulationRule)은 생태자연도 1등급도, 백두대간
핵심구역도 IMPOSSIBLE이 아니라 CONDITIONAL로 본다. "원칙적으로 지양하나
법률상 예외 행위가 있다"는 것이 조문에 근거한 판단이기 때문이다.
IMPOSSIBLE로 등록된 레이어는 비행금지구역 하나뿐이다.

여기서 '배제구역'을 임의로 정하면, 코드가 법령에 없는 금지를 만들어내는
셈이 된다. 그래서 등급별 면적을 그대로 내고, 가용면적은 **두 가지로
병기**한다. 어느 쪽을 쓸지는 사업 판단이지 계산의 몫이 아니다.

    엄격 가용   = 아무 규제 레이어에도 걸리지 않는 면적
    협의 포함   = 엄격 가용 + 조건부 면적 (협의·저감으로 진행 가능한 범위)

■ 면적 4분할

    배제      IMPOSSIBLE 판정 레이어 ∪ 조례 이격거리 위반 범위
    조건부    CONDITIONAL 판정 레이어 (사유별 면적을 함께 낸다)
    제약없음  위 어디에도 걸리지 않음
    보류      조례를 확인하지 못한 지자체 조각 · 조회 실패 레이어

보류를 따로 세는 이유는 [[jurisdiction]] 모듈 주석과 같다. 모르는 것을
가용으로 세면 사업에 유리하게 틀리고, 배제로 세면 멀쩡한 부지를 버린다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import re

from . import buildings, geo, jurisdiction
from .providers.vworld import VworldClient
from .schemas import Status

logger = logging.getLogger(__name__)

#: 레이어 조회 동시 실행 수. V-World에 과도한 동시요청을 보내지 않는다.
MAX_WORKERS = 6

#: 이격 버퍼를 씌울 대상 시설 레이어 (도로명주소건물).
#: 조례의 '주거밀집지역·정온시설'에 대응하는 실물이다.
FACILITY_LAYER = 'lt_c_spbd'

#: 이격 대상 시설을 구역 밖 어디까지 모을지. 가장 큰 조례 반경보다 넉넉해야
#: 경계 바로 밖 마을이 누락되지 않는다. 조례 최대값에 이 여유를 더해 쓴다.
FACILITY_MARGIN_M = 500

#: 이격 대상 시설을 뭉칠 격자 크기(m)와, 그로 인한 오차를 덮는 반경 여유.
#: 여유는 격자 대각선의 절반(=grid/√2) 이상이어야 뭉치면서 좁아지는 곳이 없다.
#: 100m 격자의 대각선 절반은 70.7m이므로 71m로 둔다.
FACILITY_GRID_M = 100
GRID_SAFETY_M = 71

#: 이 비율 이상을 덮는 레이어는 구역 내 위치를 가르지 못하므로 면적 분할에서
#: 빼고 '구역 전체 조건'으로 따로 표기한다. 판정 자체를 지우는 것이 아니다.
BLANKET_RATIO = 0.995

#: 용도지역 4종. 국토계획법 제36조가 **전 국토를 빈틈없이** 도시·관리·농림·
#: 자연환경보전으로 나눈 것이라, 어느 구역을 그려도 이 넷을 합치면 100%가 된다.
#:
#: 보호구역·지구 같은 '지정'과 성격이 다르다. 지정은 "여기가 걸린다"를 가르지만
#: 용도지역은 "여기가 어떤 땅인가"라는 분류다. 같이 집계하면 산간 구역에서
#: 농림지역 97%가 조건부 면적을 먹어버려 나머지 제약이 전부 묻힌다.
#: 그래서 별도 축으로 뺀다. 판정에서 지우는 것이 아니라 따로 세는 것이다.
#:
#: role 필드로는 가를 수 없다 — 도시·관리는 CONTEXT인데 농림·자연환경보전은
#: REGULATION으로 등록돼 있어 넷이 엇갈린다.
ZONING_LAYER_IDS = {
    'lt_c_uq111',   # 도시지역
    'lt_c_uq112',   # 관리지역
    'lt_c_uq113',   # 농림지역
    'lt_c_uq114',   # 자연환경보전지역
}

#: 면적 제약으로 세지 않는 role.
#:   CONTEXT  참고 정보 (산림입지도 등) — 규제가 아니다
#:   PARCEL   필지 도형 (연속지적도) — 규제가 아니다
#:   DISTANCE 이격 '대상'(하천·건물·도로). 도형 자체가 제약 구역인 것이 아니라
#:            여기서 몇 m 떨어져야 하는지가 제약이다. 버퍼로 다뤄야 하고,
#:            도형을 그대로 빼면 하천 폭만큼만 빠져 판정이 왜곡된다.
NON_CONSTRAINT_ROLES = ('CONTEXT', 'PARCEL', 'DISTANCE')


def _layer_geoms(area_geom, layer) -> tuple[list, str]:
    """
    레이어 하나를 구역 범위로 조회해 (도형 목록, 오류) 를 돌려준다.

    **예외를 밖으로 내보내지 않는다.** 53개 레이어를 도는 중 하나가 500을
    돌려주면(V-World는 간헐적으로 그런다) 그 예외가 스레드풀을 타고 올라와
    구역 검토 전체가 실패한다. 레이어 하나의 일시적 장애로 나머지 52개
    판정까지 버릴 이유가 없다. 실패는 fetch_failures로 올라가 '보지 못한
    제약이 있다'는 경고로 화면에 표시된다.
    """
    try:
        feats, meta = VworldClient.fetch_area(layer.layer_id, area_geom)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('%s(%s) 조회 실패: %s', layer.title, layer.layer_id, e)
        return [], f'{type(e).__name__}'
    if meta.get('strategy') == 'failed':
        return [], meta.get('error') or '조회 실패'
    out = []
    for f in feats:
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        gm = geo.to_metric(g)
        clipped = geo.clip(gm, area_geom)
        if clipped is not None:
            out.append(clipped)
    # 타일 상한에 걸려 구역 일부를 못 봤으면 '없음'으로 읽히면 안 된다
    return out, ('구역 일부만 조회됨' if meta.get('truncated') else '')


def _grandfathering(slices, permit_date) -> dict:
    """
    조례 시행일과 발전사업허가일을 대조해 경과규정 검토 대상인지 표시한다.

    **면제를 판정하지 않는다.** 부칙이 그 사업에 적용되는지는 관할 지자체가
    판단할 문제이고, 문언도 조례마다 다르다("허가를 받은 경우"인지 "실시계획
    승인"인지 "착공"인지). 여기서는 날짜가 앞선다는 사실과 부칙 원문을
    보여주는 데까지가 역할이다.

    발전사업허가일을 받지 않았어도 조례 시행일은 항상 싣는다. 그것만으로도
    "우리 허가는 그 전인데?"라는 검토가 촉발된다.
    """
    rows = []
    flagged = False
    for s in slices:
        for r in s.get('rules') or []:
            eff = getattr(r, 'effective_date', None)
            # 소급 여부를 가르는 날짜는 조례 최신 시행일이 아니라 **경과조치를
            # 담은 개정의 시행일**이다. 그 값을 못 구했을 때만 최신 시행일로
            # 대신하고, 어느 쪽을 썼는지 화면에 밝힌다.
            gf = getattr(r, 'grandfather_date', None)
            cutoff = gf or eff
            if not cutoff:
                continue
            earlier = bool(permit_date and permit_date < cutoff)
            flagged = flagged or earlier
            rows.append({
                'sigungu': s['sigungu'],
                'ordinance': r.ordinance_name,
                'article': r.article,
                'effective_date': eff.isoformat() if eff else '',
                'cutoff_date': cutoff.isoformat(),
                'cutoff_is_transition': bool(gf),
                'cutoff_basis': getattr(r, 'grandfather_basis', '') or '',
                'permit_earlier': earlier,
                'addenda': (r.addenda or '')[:2000],
            })
            break                     # 지자체당 한 건이면 충분하다
    return {
        'permit_date': permit_date.isoformat() if permit_date else '',
        # True면 '조례 시행일보다 허가일이 앞선다'는 사실만 뜻한다. 면제 확정이 아니다.
        'review_required': flagged,
        'ordinances': rows,
        'note': ('발전사업허가일이 조례 시행일보다 앞섭니다. 부칙 경과조치에 따라 '
                 '종전 기준이 적용될 수 있으므로 관할 지자체 확인이 필요합니다. '
                 '아래 "조례 이격 미적용 시" 값은 참고용이며 면제 확정이 아닙니다.'
                 if flagged else
                 '발전사업허가일을 입력하면 조례 시행일과 대조해 경과규정 검토 '
                 '대상 여부를 표시합니다.' if not permit_date else
                 '발전사업허가일이 조례 시행일 이후이므로 현행 조례가 적용됩니다.'),
    }


def _rule_set(rules) -> dict:
    """
    지자체 조례를 항목별로 정리한다.

    반환 {'house_n': 5, 'house_ge': 2000, 'house_lt': 2000,
          'livestock': 2000, 'quiet': 1000}
    값이 없으면 키가 빠진다 — 조례에 없는 대상에 임의로 거리를 붙이지 않는다.
    """
    out: dict = {}
    for r in rules:
        d = (r.target_detail or '')
        if r.target == 'RESIDENTIAL':
            m = re.search(r'(\d+)\s*호', d)
            if m:
                out['house_n'] = int(m.group(1))
            # '5호 미만'처럼 미만을 명시한 행이 하위 구간이다.
            key = 'house_lt' if '미만' in d else 'house_ge'
            out[key] = max(out.get(key, 0), r.distance_m)
        elif r.target == 'QUIET_FACILITY':
            out['quiet'] = max(out.get('quiet', 0), r.distance_m)
        elif '축사' in d or '가축' in d:
            out['livestock'] = max(out.get('livestock', 0), r.distance_m)
    # 한쪽만 있으면 같은 값으로 본다 (구간 구분이 없는 조례)
    if 'house_ge' in out and 'house_lt' not in out:
        out['house_lt'] = out['house_ge']
    return out


def _facility_buffers(area_geom, slices, separation_zone=None) -> tuple[dict, list[str]]:
    """
    지자체 조각별로 조례 이격거리 버퍼를 만든다.

    종전에는 용도를 가리지 않고 **모든 건물**에 조례 최대 반경을 씌워
    검토 구역의 97.8%가 배제로 잡혔다. 조례가 규율하는 것은 주택·축사·
    정온시설이고 부속 건축물은 명시적으로 제외되는데, 창고 한 채까지
    주거 2,000m를 만들어 내고 있었다.

    이제 [[buildings]]가 건축물대장 주용도로 갈라준 것을 항목별로 쓴다.
      · 주택   50m 군집 → N호 이상/미만으로 거리를 나눠 적용
      · 축사   축사 항목 거리
      · 정온   정온시설 항목 거리
      · 창고·부속건축물  버퍼 없음
      · 대장 미등재      버퍼 없음. 다만 '용도 미확인 N동'으로 올려 조건부로 남긴다

    시설은 행정구역을 가리지 않고 모은다. 조문이 관할구역으로 한정하지 않는
    것이 일반적이라 경계 너머 마을도 이격 대상이다. 거리만 조각별 조례를 따른다.

    separation_zone을 주면 그 안에서만 이격 위반을 센다. 배치선 검토에서
    발전기 지점만 넘기는 용도다 — 조례가 규율하는 것은 발전시설이지
    지중 집전선로나 진입도로가 아니다.
    """
    rule_sets = {s['code']: _rule_set(s['rules']) for s in slices if s['rules']}
    max_dist = max((v for rs in rule_sets.values() for v in rs.values()
                    if isinstance(v, int)), default=0)
    if not max_dist:
        return {}, []

    search = area_geom.buffer(max_dist + FACILITY_MARGIN_M)
    try:
        feats, meta = VworldClient.fetch_area(FACILITY_LAYER, search)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('이격 대상 시설 조회 실패: %s', e)
        return {}, [f'이격 대상 시설을 조회하지 못했습니다 ({type(e).__name__}). '
                    f'조례 이격거리 제약이 결과에 반영되지 않았습니다.']

    notes: list[str] = []
    if meta.get('strategy') == 'failed':
        return {}, [f'이격 대상 시설 조회 실패: {meta.get("error")}']
    if meta.get('truncated'):
        notes.append('이격 대상 시설이 일부만 조회되었습니다 (구역이 넓어 잘림).')
    if not feats:
        return {}, notes + ['구역 주변에서 이격 대상 시설이 조회되지 않았습니다.']

    codes = [s['code'] for s in slices]
    cls = buildings.classify(feats, codes)
    c = cls['counts']
    notes.append(
        '이격 대상 시설 %d동 분류 — 주택 %d · 축사 %d · 정온시설 %d · '
        '대상 아님(창고·부속 등) %d · **용도 미확인 %d**'
        % (sum(c.values()), c.get(buildings.CAT_HOUSING, 0),
           c.get(buildings.CAT_LIVESTOCK, 0), c.get(buildings.CAT_QUIET, 0),
           c.get(buildings.CAT_NOT_TARGET, 0) + c.get(buildings.CAT_ANNEX, 0),
           c.get(buildings.CAT_UNKNOWN, 0)))
    if c.get(buildings.CAT_UNKNOWN):
        notes.append(
            '용도 미확인 %d동은 건축물대장에 등재되지 않은 건물입니다. 무허가·농막·'
            '폐가일 수도, 실거주 중인 주택일 수도 있어 이격 대상 여부를 단정할 수 '
            '없습니다. 버퍼를 씌우지 않았으므로 현장 확인이 필요합니다.'
            % c[buildings.CAT_UNKNOWN])
    notes.append(
        '조례는 주민등록 실거주 주택만을 대상으로 하고 빈집을 제외하나, 그 정보는 '
        '공개되지 않습니다. 이 검토의 조례 이격 면적은 대장상 주택 기준의 '
        '상한선이며 확정치가 아닙니다.')

    # 주택은 조례가 정한 거리로 군집을 만들어 호수를 센다
    house_groups = buildings.clusters(cls[buildings.CAT_HOUSING])
    by_slice: dict[str, object] = {}
    for s in slices:
        rs = rule_sets.get(s['code'])
        if not rs:
            continue
        parts = []
        n_req = rs.get('house_n', 0)
        for g in house_groups:
            d = rs.get('house_ge' if (n_req and len(g) >= n_req) else 'house_lt')
            if d:
                parts.append(geo.union(g).buffer(d))
        for cat, key in ((buildings.CAT_LIVESTOCK, 'livestock'),
                         (buildings.CAT_QUIET, 'quiet')):
            d = rs.get(key)
            if d and cls[cat]:
                parts.append(geo.union(cls[cat]).buffer(d))
        piece = geo.clip(geo.union(parts), s['geom']) if parts else None
        if piece is not None and separation_zone is not None:
            piece = geo.clip(piece, separation_zone)
        if piece is not None:
            by_slice[s['code']] = piece
    return by_slice, notes


#: 배치선 검토 기본 반경(m). 발전기는 이격 검토가 필요해 넓게, 연결선은
#: 폭이 좁은 선형 시설이라 좁게 잡는다. 화면에서 조정할 수 있다.
DEFAULT_TURBINE_RADIUS_M = 500
DEFAULT_CORRIDOR_RADIUS_M = 100


def compute(area_ring: list, permit_date=None) -> dict:
    """
    사업구역(폴리곤)의 제약도를 만든다.

    area_ring: [(lat, lng), …] 사업구역 꼭짓점
    """
    area = geo.polygon_metric(area_ring)
    if area is None:
        raise ValueError('사업구역 폴리곤이 유효하지 않습니다 (꼭짓점 3개 이상 필요).')
    return _compute(area, permit_date=permit_date)


def compute_layout(turbines: list,
                   turbine_radius_m: int = DEFAULT_TURBINE_RADIUS_M,
                   corridor_radius_m: int = DEFAULT_CORRIDOR_RADIUS_M,
                   permit_date=None) -> dict:
    """
    발전기 배치선의 제약도를 만든다.

    turbines: [(lat, lng), …] 1호기부터 순서대로. 찍은 순서가 곧 연결 순서다.

    검토 대상 = 발전기 원들 ∪ 그 사이를 잇는 회랑

    이격거리 조례는 **발전기 원에만** 적용한다. 조문이 규율하는 것은
    '풍력발전시설'이고 소음원도 발전기지, 지중 집전선로나 진입도로가 아니다.
    연결선 구간까지 주거 2,000m를 적용하면 마을 옆을 지나는 도로 한 구간
    때문에 멀쩡한 발전기 위치까지 배제로 잡힌다.
    """
    if len(turbines) < 1:
        raise ValueError('발전기 위치를 1기 이상 지정해야 합니다.')
    spots = geo.circles(turbines, turbine_radius_m)
    route = geo.corridor(turbines, corridor_radius_m)
    area = geo.union([spots, route])
    if area is None:
        raise ValueError('배치선으로 검토 구역을 만들지 못했습니다.')
    return _compute(area, separation_zone=spots, permit_date=permit_date, layout={
        'turbines': [[round(a, 6), round(o, 6)] for a, o in turbines],
        'turbine_radius_m': turbine_radius_m,
        'corridor_radius_m': corridor_radius_m,
        'turbine_area_m2': float(spots.area) if spots is not None else 0.0,
        'corridor_area_m2': float(geo.subtract(route, spots).area)
                            if route is not None and geo.subtract(route, spots) is not None else 0.0,
    })


def _compute(area, separation_zone=None, layout: dict | None = None,
             permit_date=None) -> dict:
    total = float(area.area)

    slices, jmeta = jurisdiction.with_ordinances(area)

    from .models import RegulationLayer, RegulationRule
    layers = list(RegulationLayer.objects.filter(is_active=True, provider='VWORLD'))
    overrides = {
        (r.layer, r.condition_key): r.status
        for r in RegulationRule.objects.filter(is_active=True)
    }

    blocked_parts, conditional_parts = [], []
    by_reason: list[dict] = []
    blanket: list[dict] = []
    zoning: list[dict] = []
    failures: list[str] = []

    def work(layer):
        return layer, _layer_geoms(area, layer)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for layer, (geoms, err) in pool.map(work, layers):
            if err:
                failures.append(f'{layer.title}: {err}')
            if not geoms:
                continue
            merged = geo.union(geoms)
            if merged is None:
                continue
            a = float(merged.area)
            status = overrides.get((layer.title, ''), layer.default_status)
            row = {'layer': layer.title, 'status': status,
                   'area_m2': a, 'ratio': a / total}

            # 용도지역은 전 국토를 덮는 분류라 제약과 축이 다르다. 따로 센다.
            if layer.layer_id in ZONING_LAYER_IDS:
                zoning.append(row)
                continue

            # 참고 레이어·필지 도형·이격 대상은 면적 제약이 아니다.
            if layer.role in NON_CONSTRAINT_ROLES:
                continue

            # POSSIBLE은 제약이 아니다.
            if status == Status.POSSIBLE.value:
                continue

            # 구역을 통째로 덮는 레이어는 '구역 안 어디가 걸리는가'를 가르지
            # 못한다. 군작전구역·접근관제구역처럼 광역 공역이 그렇다. 판정을
            # 바꾸지는 않되(협의 대상인 것은 사실이다) 면적 분할에서 빼고
            # 구역 전체 조건으로 따로 세운다. 그러지 않으면 어느 구역을 그려도
            # 조건부 100%가 나와 나머지 레이어가 전부 묻힌다.
            if a / total >= BLANKET_RATIO:
                row['blanket'] = True
                blanket.append(row)
                continue

            (blocked_parts if status == Status.IMPOSSIBLE.value
             else conditional_parts).append(merged)
            by_reason.append(row)

    buffers, buf_notes = _facility_buffers(area, slices, separation_zone)
    # 조례 이격거리는 조문이 직접 금지하는 범위라 원래 배제로 세야 한다.
    # 그런데 지금 쓰는 건물 레이어에는 **용도 구분이 없다.** 창고에까지
    # 주거밀집 2,000m를 씌우면 과대 배제가 된다(삼척 12km² 구역에서 73%).
    # 확인되지 않은 것을 금지로 단정하지 않고 조건부로 둔다. 건물 용도
    # 분류가 붙으면 확인된 주거·정온시설만 배제로 옮긴다.
    ord_parts = []
    for code, piece in buffers.items():
        conditional_parts.append(piece)
        ord_parts.append(piece)
        by_reason.append({
            'layer': f'조례 이격거리 ({code})',
            'status': Status.CONDITIONAL.value,
            'area_m2': float(piece.area),
            'ratio': float(piece.area) / total,
            'provisional': True,
        })
    if buffers:
        buf_notes.append(
            '조례 이격은 대장상 주택·축사·정온시설에만 적용했습니다. 실거주·빈집 '
            '여부는 확인할 수 없어 조건부로 집계합니다.')

    blocked = geo.union(blocked_parts)
    conditional = geo.subtract(geo.union(conditional_parts), blocked) \
        if conditional_parts else None

    free = geo.subtract(geo.subtract(area, blocked), conditional)
    pending_m2 = jmeta.get('pending_area_m2', 0.0)

    # 조례 개정 전에 발전사업허가를 받은 사업은 부칙 경과조치로 종전 기준이
    # 적용될 수 있다. 적용 여부는 관할 지자체가 판단하므로 **여기서 빼지 않고**,
    # 뺐을 때의 값을 함께 낸다. 이 항목 하나가 결론을 통째로 뒤집기 때문에
    # 언급하지 않으면 보고서가 사실과 크게 다른 결론을 내게 된다.
    grand = _grandfathering(slices, permit_date)
    if ord_parts:
        ord_union = geo.union(ord_parts)
        cond_wo = geo.subtract(conditional, ord_union) if conditional is not None else None
        free_wo = geo.subtract(geo.subtract(area, geo.subtract(blocked, ord_union)),
                               cond_wo)
        grand['free_if_exempt_m2'] = float(free_wo.area) if free_wo is not None else 0.0
        grand['ordinance_area_m2'] = float(ord_union.area) if ord_union is not None else 0.0

    def m2(g):
        return float(g.area) if g is not None else 0.0

    blocked_m2, cond_m2, free_m2 = m2(blocked), m2(conditional), m2(free)
    by_reason.sort(key=lambda r: r['area_m2'], reverse=True)

    return {
        'total_area_m2': total,
        'blocked_m2': blocked_m2,
        'conditional_m2': cond_m2,
        'free_m2': free_m2,
        # 조례를 확인하지 못한 지자체 조각. 위 셋과 겹칠 수 있으므로
        # 합계에 더하지 않고 따로 표기한다.
        'pending_m2': pending_m2,
        # 가용면적은 정의가 하나가 아니다. 둘 다 내고 선택은 사용자에게 맡긴다.
        'available_strict_m2': free_m2,
        'available_with_consultation_m2': free_m2 + cond_m2,
        'by_reason': by_reason,
        # 구역 전체를 덮어 위치를 가르지 못하는 레이어. 면적 분할에는 넣지
        # 않았지만 협의 대상인 것은 사실이므로 반드시 함께 보여준다.
        'blanket': blanket,
        # 용도지역 구성. 제약 면적과는 별도 축이며, 합치면 대체로 구역 전체가 된다.
        'zoning': sorted(zoning, key=lambda r: r['area_m2'], reverse=True),
        'jurisdictions': [
            {k: v for k, v in s.items() if k not in ('geom', 'rules')}
            | {'rule_count': len(s['rules']),
               'max_distance_m': max((r.distance_m for r in s['rules']), default=0)}
            for s in slices
        ],
        'jurisdiction_meta': {k: v for k, v in jmeta.items()},
        # 조회하지 못한 레이어가 있으면 그만큼 제약을 덜 본 것이다.
        # 가용면적이 실제보다 크게 나올 수 있으므로 반드시 함께 읽어야 한다.
        'fetch_failures': failures,
        'notes': buf_notes,
        # 배치선 검토일 때만 채워진다. 폴리곤 검토면 None.
        'layout': layout,
        # 조례 경과규정 검토 — 시스템은 판정하지 않고 근거와 시나리오만 제시한다.
        'grandfathering': grand,
        'geoms': {'area': area, 'blocked': blocked,
                  'conditional': conditional, 'free': free},
    }


# ======================================================================
# 호기별 지점 검토
# ======================================================================
def evaluate_points(points: list, radius_m: int, capacity_mw=None,
                    label: str = '호기') -> list[dict]:
    """
    지점마다 기존 62개 항목 검토를 돌린다.

    구역 제약도는 '면적이 어떻게 나뉘는가'에 답하지만, 규제 항목별 가부는
    지점에서만 성립한다. 배치선 검토에서 그 둘이 다 필요하다 — 어느 호기가
    무엇에 걸리는지 알아야 배치를 고칠 수 있기 때문이다.

    지점 간은 **순차**로 돈다. 각 지점 내부가 이미 스레드풀이라, 지점까지
    동시에 돌리면 외부 API에 과도한 동시요청이 간다(engine.compare와 같은 이유).

    반환 [{'no','lat','lng','address','sido','sigungu','result'}]
    """
    from .engine import evaluate
    from .geocode import reverse_geocode

    out = []
    for i, (lat, lng) in enumerate(points, start=1):
        addr = sido = sigungu = ''
        try:
            g = reverse_geocode(lat, lng) or {}
            addr = g.get('address') or g.get('road_address') or ''
            sido, sigungu = g.get('sido', ''), g.get('sigungu', '')
        except Exception:                                       # noqa: BLE001
            logger.warning('%d%s 역지오코딩 실패', i, label)
        try:
            res = evaluate(lat=lat, lng=lng, radius_m=radius_m, address=addr,
                           capacity_mw=capacity_mw, sido=sido, sigungu=sigungu)
        except Exception as e:                                  # noqa: BLE001
            logger.exception('%d%s 검토 실패', i, label)
            out.append({'no': i, 'lat': lat, 'lng': lng, 'address': addr,
                        'sido': sido, 'sigungu': sigungu, 'result': None,
                        'error': type(e).__name__})
            continue
        out.append({'no': i, 'lat': lat, 'lng': lng, 'address': addr,
                    'sido': sido, 'sigungu': sigungu, 'result': res})
    return out


#: 항목 상태의 서열 — 여러 지점의 결과를 합칠 때 '가장 나쁜 값'을 고른다.
#: 한 호기라도 불가면 그 항목은 불가로 보고해야 한다. 평균을 내면 묻힌다.
_SEVERITY = {'IMPOSSIBLE': 3, 'UNKNOWN': 2, 'CONDITIONAL': 1, 'POSSIBLE': 0}


def merge_items(evals: list[dict]) -> list[dict]:
    """
    호기별 검토를 항목 단위로 합친다.

    반환 [{'category','item_name','status','worst_no','hits','reason',
           'per_point': {호기번호: status}}]
    """
    merged: dict[str, dict] = {}
    for ev in evals:
        res = ev.get('result')
        if not res:
            continue
        for it in res.analysis_items:
            row = merged.setdefault(it.item_name, {
                'category': it.category, 'item_name': it.item_name,
                'status': 'POSSIBLE', 'worst_no': None, 'reason': '',
                'law': it.law, 'article': it.article,
                'unknown_reason': '', 'per_point': {},
            })
            s = it.status.value
            row['per_point'][ev['no']] = s
            if _SEVERITY[s] > _SEVERITY[row['status']]:
                row.update(status=s, worst_no=ev['no'], reason=it.reason,
                           unknown_reason=it.unknown_reason)
    rows = list(merged.values())
    for r in rows:
        r['hits'] = sum(1 for v in r['per_point'].values()
                        if _SEVERITY[v] >= _SEVERITY[r['status']] > 0)
        r['total'] = len(r['per_point'])
    rows.sort(key=lambda r: (-_SEVERITY[r['status']], r['category'], r['item_name']))
    return rows
