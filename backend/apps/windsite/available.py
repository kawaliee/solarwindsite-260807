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

from . import geo, jurisdiction
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
    """레이어 하나를 구역 범위로 조회해 (도형 목록, 오류) 를 돌려준다."""
    feats, meta = VworldClient.fetch_area(layer.layer_id, area_geom)
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


def _facility_buffers(area_geom, slices) -> tuple[dict, list[str]]:
    """
    지자체 조각별로 그 지자체의 이격거리만큼 버퍼를 씌운다.

    시설은 행정구역을 가리지 않고 모은다. 조문이 "주거밀집지역으로부터
    직선거리 N미터"라고만 하고 관할구역으로 한정하지 않는 것이 일반적이라,
    경계 너머 마을도 이격 대상이다. 반경만 조각별 조례를 따른다.
    """
    max_dist = max((r.distance_m for s in slices for r in s['rules']), default=0)
    if not max_dist:
        return {}, []

    search = area_geom.buffer(max_dist + FACILITY_MARGIN_M)
    feats, meta = VworldClient.fetch_area(FACILITY_LAYER, search)
    notes: list[str] = []
    if meta.get('strategy') == 'failed':
        return {}, [f'이격 대상 시설 조회 실패: {meta.get("error")}']
    if meta.get('truncated'):
        notes.append('이격 대상 시설이 일부만 조회되었습니다 (구역이 넓어 잘림).')

    # 건물 도형을 그대로 버퍼링하면 끝나지 않는다. 영양 16km² 구역에서
    # 5,711동을 합치면 정점이 67,754개인데, 여기에 2km 버퍼를 씌우는 연산이
    # 300초를 넘겨도 끝나지 않았다(파싱 0.8s · union 0.5s는 문제가 아니다).
    #
    # 2km 반경 앞에서 건물 하나의 모양은 의미가 없으므로 격자로 뭉친다.
    # 대신 반경에 격자 대각선의 절반을 더해, 뭉치면서 잘려나가는 부분이
    # 없도록 한다. 결과는 참값보다 **넓어질 뿐 좁아지지 않는다** —
    # 이격 판정에서 좁아지는 오차는 위반을 놓치는 것이라 허용할 수 없다.
    cells: set[tuple[int, int]] = set()
    for f in feats:
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        c = geo.to_metric(g).centroid
        cells.add((int(c.x // FACILITY_GRID_M), int(c.y // FACILITY_GRID_M)))
    if not cells:
        return {}, notes + ['구역 주변에서 이격 대상 시설이 조회되지 않았습니다.']

    half = FACILITY_GRID_M / 2.0
    merged = geo.union([
        geo.Point(i * FACILITY_GRID_M + half, j * FACILITY_GRID_M + half)
        for i, j in cells
    ])
    notes.append(
        f'이격 대상 시설 {len(feats):,}동을 {FACILITY_GRID_M}m 격자 {len(cells):,}개로 '
        f'묶어 계산했습니다 (반경에 {GRID_SAFETY_M}m를 더해 과소 배제를 막았습니다).')
    by_slice: dict[str, object] = {}
    for s in slices:
        if not s['rules']:
            continue
        # 같은 조각 안에서도 대상별 반경이 다르다. 가장 엄격한 값을 쓰면
        # 도로 500m 기준이 주거 2,000m로 부풀어 과대 배제가 된다. 대신
        # 대상 구분 없이 시설 위치만 알고 있으므로, 현 단계에서는 조례
        # 최대값을 쓰고 대상별 분리는 시설 분류가 붙은 뒤로 미룬다.
        d = max(r.distance_m for r in s['rules'])
        buf = merged.buffer(d + GRID_SAFETY_M)
        piece = geo.clip(buf, s['geom'])
        if piece is not None:
            by_slice[s['code']] = piece
    return by_slice, notes


def compute(area_ring: list) -> dict:
    """
    사업구역의 제약도를 만든다.

    area_ring: [(lat, lng), …] 사업구역 꼭짓점
    """
    area = geo.polygon_metric(area_ring)
    if area is None:
        raise ValueError('사업구역 폴리곤이 유효하지 않습니다 (꼭짓점 3개 이상 필요).')
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

    buffers, buf_notes = _facility_buffers(area, slices)
    # 조례 이격거리는 조문이 직접 금지하는 범위라 원래 배제로 세야 한다.
    # 그런데 지금 쓰는 건물 레이어에는 **용도 구분이 없다.** 창고에까지
    # 주거밀집 2,000m를 씌우면 과대 배제가 된다(삼척 12km² 구역에서 73%).
    # 확인되지 않은 것을 금지로 단정하지 않고 조건부로 둔다. 건물 용도
    # 분류가 붙으면 확인된 주거·정온시설만 배제로 옮긴다.
    for code, piece in buffers.items():
        conditional_parts.append(piece)
        by_reason.append({
            'layer': f'조례 이격거리 (용도 미확인 건물 기준, {code})',
            'status': Status.CONDITIONAL.value,
            'area_m2': float(piece.area),
            'ratio': float(piece.area) / total,
            'provisional': True,
        })
    if buffers:
        buf_notes.append(
            '이격 버퍼는 용도가 확인되지 않은 건물 전체에 조례 최대 반경을 씌운 '
            '값이라 실제보다 넓습니다. 배제가 아닌 조건부로 집계했습니다.')

    blocked = geo.union(blocked_parts)
    conditional = geo.subtract(geo.union(conditional_parts), blocked) \
        if conditional_parts else None

    free = geo.subtract(geo.subtract(area, blocked), conditional)
    pending_m2 = jmeta.get('pending_area_m2', 0.0)

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
        'geoms': {'area': area, 'blocked': blocked,
                  'conditional': conditional, 'free': free},
    }
