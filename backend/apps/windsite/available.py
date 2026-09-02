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

    배제      IMPOSSIBLE 판정 레이어 ∪ **조례 주거·도로 이격 범위**
    조건부    CONDITIONAL 판정 레이어 ∪ 조례 축사·정온시설 이격
              (사유별 면적을 함께 낸다)
    제약없음  위 어디에도 걸리지 않음
    보류      조례를 확인하지 못한 지자체 조각 · 조회 실패 레이어

■ ⚠️ 경과규정이 있으면 조례 이격을 배제로 세지 않는다

발전사업허가일이 조례(경과조치) 시행일보다 앞서면 부칙에 따라 종전 기준이
적용될 수 있다. 그런 사업을 이격으로 배제하면 **실제로 진행 중인 사업지가
보고서에서 사업 불가로 못 박힌다**(삼척 천봉풍력이 그런 경우다).

그렇다고 빼 주지도 않는다. 부칙이 그 사업에 적용되는지는 관할 지자체가
판단할 문제다. **배제가 아니라 조건부**로 두고 까닭을 밝힌다.

보류를 따로 세는 이유는 [[jurisdiction]] 모듈 주석과 같다. 모르는 것을
가용으로 세면 사업에 유리하게 틀리고, 배제로 세면 멀쩡한 부지를 버린다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import re

from . import (buildings, energy as energy_mod, geo, jobs, jurisdiction,
               ordinances, parcels, roads)
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
          'livestock': 2000, 'quiet': 1000,
          'road_major': 1000, 'road_minor': 500}
    값이 없으면 키가 빠진다 — 조례에 없는 대상에 임의로 거리를 붙이지 않는다.

    road_major는 고속도로·국도, road_minor는 지방도·군도다. 조례가 둘을
    다른 거리로 규율하므로 합치지 않는다.
    """
    out: dict = {}
    unhandled: list[str] = []
    for r in rules:
        d = (r.target_detail or '')
        if ordinances.COUNT_RADIUS_TAG in d:
            # 이격거리가 아니라 **호수를 세는 반경**이다. 조례가 이 방식을
            # 쓰면 주택 간 연쇄 군집(50m) 대신 이 반경으로 호수를 센다.
            out['house_radius'] = max(out.get('house_radius', 0), r.distance_m)
        elif r.target == 'RESIDENTIAL':
            m = re.search(r'(\d+)\s*호', d)
            if m:
                out['house_n'] = int(m.group(1))
            # '5호 미만'처럼 미만을 명시한 행이 하위 구간이다.
            key = 'house_lt' if '미만' in d else 'house_ge'
            out[key] = max(out.get(key, 0), r.distance_m)
        elif r.target == 'ROAD':
            # 도로는 조례마다 등급을 가르기도 하고 안 가르기도 한다.
            #
            #   장흥군  "고속도로와 국도에서는 1천미터, 지방도와 군도에서는 500미터"
            #           → 등급별로 다른 거리. 하나로 합치면 한쪽이 틀어진다.
            #   삼척군  "도로에서 1,000미터"
            #           → 등급을 가리지 않는다. **모든 도로**에 적용해야 한다.
            #   완도군  "국도·지방도·군도·농어촌도로에서 1,000미터"
            #           → 열거된 등급 **전부**에 적용해야 한다.
            #
            # 종전에는 '고속|국도'가 있으면 major, 없으면 minor로 **하나만**
            # 골랐다. 그래서 삼척 '도로 1,000m'가 국도에 적용되지 않고(과소),
            # 완도 '국도·지방도·군도'가 국도에만 적용됐다(역시 과소).
            major = bool(re.search(r'고속|국도', d))
            minor = bool(re.search(r'지방도|군도|시도|시·군도|농어촌', d))
            for key in (('road_major',) if major and not minor
                        else ('road_minor',) if minor and not major
                        else ('road_major', 'road_minor')):
                out[key] = max(out.get(key, 0), r.distance_m)
            # 조례가 등급을 가리지 않거나 농어촌도로까지 들면, 국가교통DB의
            # '시·군도'(군도 + 농어촌도로)가 통째로 대상이다. 그때는 조건부가
            # 아니라 **배제**로 세야 한다.
            if not (major or minor) or '농어촌' in d:
                out['road_minor_certain'] = True
        elif r.target == 'QUIET_FACILITY':
            out['quiet'] = max(out.get('quiet', 0), r.distance_m)
        # 조례마다 부르는 이름이 다르다. 완도군은 '생산시설(축사, 축양장 등)',
        # 삼척시는 '축사'로 적는다. 이름이 안 맞으면 축사 이격이 통째로 빠진다.
        elif any(k in d for k in ('축사', '가축', '축양장', '생산시설', '畜')):
            out['livestock'] = max(out.get('livestock', 0), r.distance_m)
        else:
            # 건물이 아닌 대상(도로 등)은 건축물 레이어로 버퍼를 만들 수 없다.
            # 조용히 버리면 제약을 놓친 채 가용면적이 넓게 나오므로 이름을 남긴다.
            # (완도군 조례 제20조의2 — 국도·지방도·군도·농어촌도로에서 1,000m)
            unhandled.append(f'{r.get_target_display()}'
                             + (f'({d})' if d else '')
                             + f' {r.distance_m:,}m')
    # 한쪽만 있으면 같은 값으로 본다 (구간 구분이 없는 조례)
    if 'house_ge' in out and 'house_lt' not in out:
        out['house_lt'] = out['house_ge']
    if unhandled:
        out['_unhandled'] = unhandled
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

    # 공간으로 평가하지 못한 조항을 먼저 알린다. 면적에서 빠졌다는 사실을
    # 말하지 않으면 가용면적이 실제보다 넓은 채로 그냥 읽힌다.
    gaps: list[str] = []
    for s in slices:
        for txt in (rule_sets.get(s['code']) or {}).get('_unhandled') or []:
            gaps.append(f'{s["sigungu"]} {txt}')
    pre: list[str] = []
    if gaps:
        pre.append(
            '조례에 있으나 면적 산출에 반영하지 못한 이격 조항 — ' + ' · '.join(gaps)
            + '. 건축물이 아닌 대상(도로 등)이라 건물 레이어로 버퍼를 만들 수 '
              '없습니다. 아래 가용면적에는 이 제약이 빠져 있으므로, 해당 조항은 '
              '도로 현황도로 별도 확인하십시오.')

    # bool은 int의 하위형이라 그냥 두면 road_minor_certain(True)이 거리 1로
    # 섞인다. 값이 작아 결과는 같지만, 읽는 사람이 헷갈린다.
    max_dist = max((v for rs in rule_sets.values() for v in rs.values()
                    if isinstance(v, int) and not isinstance(v, bool)), default=0)
    if not max_dist:
        return {}, pre

    search = area_geom.buffer(max_dist + FACILITY_MARGIN_M)
    try:
        feats, meta = VworldClient.fetch_area(FACILITY_LAYER, search)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('이격 대상 시설 조회 실패: %s', e)
        return {}, pre + [f'이격 대상 시설을 조회하지 못했습니다 ({type(e).__name__}). '
                          f'조례 이격거리 제약이 결과에 반영되지 않았습니다.']

    notes: list[str] = list(pre)
    if meta.get('strategy') == 'failed':
        return {}, pre + [f'이격 대상 시설 조회 실패: {meta.get("error")}']
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
        '공개되지 않습니다. 이 검토의 조례 주거 이격 면적은 대장상 주택 기준의 '
        '**상한선**이며 확정치가 아닙니다 — 빈집이 섞여 있으면 실제 불가 면적은 '
        '이보다 좁습니다. 현장에서 실거주 여부를 확인하면 배제 면적이 줄어듭니다.')

    # 주택은 조례가 정한 거리로 군집을 만들어 호수를 센다
    house_groups = buildings.clusters(cls[buildings.CAT_HOUSING])
    by_slice: dict[str, dict] = {}
    for s in slices:
        rs = rule_sets.get(s['code'])
        if not rs:
            continue
        # **주거와 나머지를 나눠 담는다.** 주거 이격은 조문이 직접 금지하는
        # 범위이고 대상(주택)이 건축물대장 주용도로 확인되므로 배제로 세지만,
        # 축사·정온시설은 조례마다 대상 범위가 달라 조건부로 남긴다.
        housing, other = [], []
        n_req = rs.get('house_n', 0)
        radius = rs.get('house_radius', 0)
        if radius and n_req:
            # 조례가 반경 방식으로 호수를 센다 — '가장 가까운 가구를 기점으로
            # 반경 N미터 안에 M호 이상'. 연쇄 군집으로 세면 마을이 조각나
            # 10호 이상이 하나도 안 나온다(장흥 실측).
            dense, sparse = buildings.dense_split(
                cls[buildings.CAT_HOUSING], radius, n_req)
            for pts, key in ((dense, 'house_ge'), (sparse, 'house_lt')):
                d = rs.get(key)
                if d and pts:
                    housing.append(geo.union(pts).buffer(d))
        else:
            for g in house_groups:
                d = rs.get('house_ge' if (n_req and len(g) >= n_req)
                           else 'house_lt')
                if d:
                    housing.append(geo.union(g).buffer(d))
        for cat, key in ((buildings.CAT_LIVESTOCK, 'livestock'),
                         (buildings.CAT_QUIET, 'quiet')):
            d = rs.get(key)
            if d and cls[cat]:
                other.append(geo.union(cls[cat]).buffer(d))

        got: dict = {}
        for name, parts in (('housing', housing), ('other', other)):
            piece = geo.clip(geo.union(parts), s['geom']) if parts else None
            if piece is not None and separation_zone is not None:
                piece = geo.clip(piece, separation_zone)
            if piece is not None and not piece.is_empty:
                got[name] = piece
        if got:
            by_slice[s['code']] = got
    return by_slice, notes


def ordinance_zones(buffers: dict) -> tuple:
    """
    조각별 버퍼 → (주거 이격 범위, 그 밖의 이격 범위).

    ⚠️ 주거 범위는 **배제**, 나머지는 조건부다. 둘을 합쳐 쓰면 축사·정온
       이격까지 사업 불가로 칠해져 과대 배제가 된다.
    """
    return (geo.union([v['housing'] for v in buffers.values() if v.get('housing')]),
            geo.union([v['other'] for v in buffers.values() if v.get('other')]))


#: 배치선 검토 기본 반경(m). 발전기는 이격 검토가 필요해 넓게, 연결선은
#: 폭이 좁은 선형 시설이라 좁게 잡는다. 화면에서 조정할 수 있다.
DEFAULT_TURBINE_RADIUS_M = 200
DEFAULT_CORRIDOR_RADIUS_M = 100


def compute(area_ring: list, permit_date=None, job_id: str = '',
            energy: str = energy_mod.DEFAULT) -> dict:
    """
    사업구역(폴리곤)의 제약도를 만든다.

    area_ring: [(lat, lng), …] 사용자가 화면에서 그린 꼭짓점

    ⚠️ 그린 폴리곤을 그대로 검토 도형으로 쓰지 않는다.

       그린 폴리곤은 필지를 고르는 올가미일 뿐, 인허가의 사업구역은 **편입
       필지들의 합**이다. 태양광 구역 검토에서는 그린 구역 안 도로·구거·
       하천 같은 '대상 아님' 필지를 빼고 **사업 대상 필지들의 합**을 검토
       도형으로 삼는다(`screening.site_geom`). 장흥 실측에서 그린 폴리곤이
       물길 26.5ha를 품고 있어, 물길 위 생태자연도 2등급이 '사업구역 내
       2등급'으로 잡히는 오탐이 났다 — 지도의 채색(필지)과 판정(그린 폴리곤)
       이 서로 다른 구역을 보고 있었던 것이다.

       필지 채색이 없는 발전원(풍력)은 종전대로 그린 폴리곤을 쓴다.
       정제에 실패해도 검토를 멈추지 않는다 — 그린 폴리곤으로 되돌아가되
       그 사실을 결과에 남긴다.
    """
    drawn = geo.polygon_metric(area_ring)
    if drawn is None:
        raise ValueError('사업구역 폴리곤이 유효하지 않습니다 (꼭짓점 3개 이상 필요).')

    site, water, site_meta = drawn, None, {}
    if energy_mod.profile(energy).has_screening:
        from . import screening
        site, water, site_meta = screening.site_geom(drawn, energy)

    out = _compute(site, permit_date=permit_date, job_id=job_id, energy=energy)
    # 원본 그린 폴리곤을 함께 남긴다 — 화면 재편집과 저장이 이 값을 쓰고,
    # 보고서는 '그린 구역'과 '사업구역(필지 기반)'의 차이를 이 값으로 밝힌다.
    out['geoms']['drawn'] = drawn
    # 지도에 경계선으로 그리는 도형 = **편입 필지(site) 그 자체**.
    #
    # 4차 요구("지적선을 따라 계단식으로 꺾이는 폴리곤 · 필지 경계와 일치")
    # 그대로다. 부동소수점 잡음만 0.3m 이내로 다듬는다 — `site_outline`
    # 참조. 그린 폴리곤(drawn)은 검토구역 표시일 뿐 경계가 아니다 — 물길
    # 위를 지나가게 그려진 구간이 있어 경계로 쓰면 "하천이 구역 안"으로
    # 읽힌다.
    out['geoms']['site_outline'] = site_outline(site, water)
    out['site_refine'] = site_meta
    # §계측 — 생성 시점 지문. 렌더러·판정이 받는 도형과 대조하는 기준값이다.
    import hashlib
    from .report_style import site_signature
    for tag, g in (('판정·면적(site)', site),
                   ('경계표시(outline)', out['geoms']['site_outline'])):
        a, v, _h = site_signature(g)
        logger.info('도형지문 | %-16s | area=%.1f | v=%d | wkb=%s',
                    tag, a, v, hashlib.md5(g.wkb).hexdigest()[:12])
    return out


#: 표시용 외곽을 다듬는 허용오차(m). simplify(preserve_topology=True)에만
#: 쓴다 — Douglas-Peucker는 각 정점을 원본에서 이 거리 안으로만 움직이므로,
#: 부풀음(overshoot)이 이 값을 넘을 수 없다.
OUTLINE_SIMPLIFY_M = 0.3


def site_outline(site, water=None):                              # noqa: ARG001
    """
    사업구역의 **표시용 외곽**.

    ⚠️ 5차 실측(2026-08)으로 폐기된 접근 — morphological closing(팽창 후
       수축)으로 좁은 농로 틈을 메워 조각 수를 74→4개로 줄였더니, 실제
       필지 경계보다 **최대 8.5%(16.2ha) 넓게 부푼 외곽**이 나왔다.
       ③ 환경성 지도는 색면(농업진흥지역도 ∩ site — 정확)과 점선(closing한
       외곽 — 부풂)을 같이 그리므로, 점선이 색면 밖으로 삐져나와 사용자가
       직접 여러 지점을 짚어 지적했다. 반경을 낮춰도 부풂과 조각 수는
       맞바꿈일 뿐 없어지지 않는다(실측: r=3m에서도 2.1%·35조각).

       판정·면적과 지도 경계가 다른 도형이면 이런 어긋남이 구조적으로
       재발한다. 그래서 **표시용과 판정용을 더 이상 나누지 않는다.**
       `site`를 원형 그대로 쓰고, 부동소수점 잡음만 아주 작은 허용오차로
       다듬는다 — simplify는 정의상 정점을 원본에서 허용오차 밖으로
       옮기지 않으므로 부풀음이 그 값(0.3m)을 넘을 수 없다. 필지가 74조각
       인 채로 그려지더라도(요청 4차의 "지적선을 따라 계단식으로 꺾이는
       폴리곤"과 정확히 같은 도형이다), 부정확한 것보다 낫다.
    """
    try:
        s = site.simplify(OUTLINE_SIMPLIFY_M, preserve_topology=True)
        return s if s is not None and not s.is_empty else site
    except Exception:                                           # noqa: BLE001
        logger.exception('표시용 외곽 생성 실패 — 원형을 그대로 씁니다')
        return site


def compute_layout(turbines: list,
                   turbine_radius_m: int = DEFAULT_TURBINE_RADIUS_M,
                   corridor_radius_m: int = DEFAULT_CORRIDOR_RADIUS_M,
                   permit_date=None, job_id: str = '',
                   energy: str = energy_mod.DEFAULT) -> dict:
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
    return _compute(area, separation_zone=spots, permit_date=permit_date,
                    job_id=job_id, energy=energy, layout={
        'turbines': [[round(a, 6), round(o, 6)] for a, o in turbines],
        'turbine_radius_m': turbine_radius_m,
        'corridor_radius_m': corridor_radius_m,
        'turbine_area_m2': float(spots.area) if spots is not None else 0.0,
        'corridor_area_m2': float(geo.subtract(route, spots).area)
                            if route is not None and geo.subtract(route, spots) is not None else 0.0,
    })


def compute_parcels(parcel_list: list, permit_date=None, job_id: str = '',
                    energy: str = energy_mod.DEFAULT) -> dict:
    """
    필지(PNU) 단위 제약도를 만든다.

    parcel_list: `parcels.resolve()` 가 돌려준 필지 목록

    검토 대상 = 필지 폴리곤들의 합집합. 원이 아니라 **경계 그대로** 쓴다.
    점+반경으로 보면 옆 필지 규제가 딸려 들어오고 이 필지 끝자락은 빠진다.

    이격거리 조례는 구역 전체에 적용한다(separation_zone을 따로 두지 않는다).
    태양광은 모듈이 부지를 덮으므로, 발전시설 위치와 사업구역이 같다.
    """
    if not parcel_list:
        raise ValueError('검토할 필지가 없습니다.')
    area = parcels.geometry(parcel_list)
    if area is None:
        raise ValueError('필지 경계로 검토 구역을 만들지 못했습니다.')
    out = _compute(area, permit_date=permit_date, job_id=job_id, energy=energy,
                   parcel=parcels.summarize(parcel_list))
    # 필지 경계를 판정 단계로 넘긴다. summarize()는 화면 응답용이라 도형을
    # 떼어내므로, 항목 평가가 쓸 원본을 따로 실어 둔다(_area_payload에서 제외).
    out['_parcel_rings'] = [p.get('rings') or [] for p in parcel_list]
    return out


def _turbine_jurisdictions(layout: dict | None, slices: list) -> list:
    """
    발전기 위치별 관할 지자체 → [{sigungu, sido, count, nos}] (기수 많은 순).

    검토 면적 기준 배분(`jurisdictions`)과 다른 값이다. 배치선 검토의 검토
    면적은 발전기 원과 연결 회랑을 부풀린 도형이라, 발전기가 한 기도 없는
    이웃 지자체가 큰 비율로 잡힌다(평창 문재풍력 — 8기 전부 평창군인데
    검토 면적으로는 횡성군 47.3%). 인허가·조례는 발전기가 실제로 서는
    자리를 따르므로 두 기준을 갈라 낸다.

    slices에 이미 지자체별 도형이 있으므로 추가 조회가 없다.
    """
    if not layout or not layout.get('turbines') or not slices:
        return []
    try:
        pts = [geo.point_metric(a, o) for a, o in layout['turbines']]
    except Exception:                                           # noqa: BLE001
        logger.exception('발전기 좌표 변환 실패 — 위치 기준 지자체를 내지 않는다')
        return []

    by: dict[str, dict] = {}
    for no, p in enumerate(pts, 1):
        hit = next((s for s in slices
                    if s.get('geom') is not None and s['geom'].contains(p)), None)
        if hit is None:                 # 경계에 걸치면 가장 가까운 조각으로
            hit = min((s for s in slices if s.get('geom') is not None),
                      key=lambda s: s['geom'].distance(p), default=None)
        if hit is None:
            continue
        slot = by.setdefault(hit['sigungu'], {'sigungu': hit['sigungu'],
                                              'sido': hit.get('sido', ''),
                                              'nos': []})
        slot['nos'].append(no)
    out = [{**v, 'count': len(v['nos'])} for v in by.values()]
    out.sort(key=lambda r: -r['count'])
    return out


def _compute(area, separation_zone=None, layout: dict | None = None,
             permit_date=None, job_id: str = '',
             energy: str = energy_mod.DEFAULT,
             parcel: dict | None = None) -> dict:
    total = float(area.area)

    # 조례는 에너지원마다 거리가 다르다. 같은 조례의 같은 별표에서도
    # 태양광 열과 풍력 열이 갈리므로, 여기서 잘못 넘기면 이격 버퍼가
    # 통째로 다른 값으로 그려진다.
    slices, jmeta = jurisdiction.with_ordinances(area, energy)

    from .models import RegulationLayer, RegulationRule
    layers = list(RegulationLayer.objects.filter(is_active=True, provider='VWORLD'))
    overrides = {
        (r.layer, r.condition_key): r.status
        for r in RegulationRule.objects.filter(is_active=True)
    }

    blocked_parts, conditional_parts = [], []
    # blanket(구역 전체를 뒤덮는 레이어)의 도형. blocked_parts/conditional_parts와
    # 나누는 이유는 §아래 참조 — by_reason 표에는 올리지 않되, 면적 합계에서는
    # 반드시 살아 있어야 한다.
    blanket_blocked, blanket_conditional = [], []
    by_reason: list[dict] = []
    blanket: list[dict] = []
    zoning: list[dict] = []
    failures: list[str] = []
    # 레이어별 원본 도형 — 면적 집계와 별개로 남겨 둔다. 보고서의 항목별
    # 환경성 평가 지도(용도지역·농업진흥·생태자연도 등)가 "무엇이 얼마나"가
    # 아니라 "어디가"를 보여줘야 하는데, by_reason에는 면적 숫자만 있고
    # 도형이 없어 지도를 그릴 수 없었다.
    layer_geoms: dict = {}

    def work(layer):
        # 스레드 안에서 확인해야 남은 레이어 조회가 실제로 멈춘다.
        # 루프 밖에서만 보면 pool.map이 이미 전부 제출한 뒤라 소용없다.
        jobs.check(job_id)
        return layer, _layer_geoms(area, layer)

    seen = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for layer, (geoms, err) in pool.map(work, layers):
            seen += 1
            jobs.set_progress(job_id, seen, len(layers), '규제 레이어 조회')
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
            layer_geoms[layer.title] = merged

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
            # 못한다. 군작전구역·접근관제구역처럼 광역 공역이 그렇다. **항목별
            # 표(by_reason)에는 올리지 않는다** — 100%짜리 항목이 표를 차지하면
            # 다른 규제의 면적·비율이 상대적으로 안 보인다.
            #
            # ⚠️ 그렇다고 배제/조건부/제약없음 4분할 합계에서 빼면 안 된다.
            #    한때 그렇게 했다가 실측(2026-08, 장흥)에서 드러난 사고 —
            #    농업진흥지역도가 사업구역(필지 정제 후)의 100.00%를 덮어
            #    blanket으로 빠지자, 그 190.5ha 전체가 **조건부가 아니라
            #    "제약없음(free·초록)"으로 계산**됐다. 필지 채색(그린 폴리곤
            #    기준 98.96%로 blanket 미해당)은 581필지를 정확히 조건부로
            #    칠하고 있었는데, 면적 합계만 다른 답을 낸 것이다. blanket도
            #    반드시 blocked/conditional 합계에는 들어가야 한다 — 표에서만
            #    빠진다.
            if a / total >= BLANKET_RATIO:
                row['blanket'] = True
                blanket.append(row)
                (blanket_blocked if status == Status.IMPOSSIBLE.value
                 else blanket_conditional).append(merged)
                continue

            (blocked_parts if status == Status.IMPOSSIBLE.value
             else conditional_parts).append(merged)
            by_reason.append(row)

    buffers, buf_notes = _facility_buffers(area, slices, separation_zone)
    # 조례 이격거리는 **조문이 직접 금지하는 범위**다.
    #
    # 종전에는 전부 조건부로 뒀다. 쓰던 건물 레이어에 용도 구분이 없어 창고
    # 한 채에도 주거 이격을 씌우면 과대 배제가 됐기 때문이다(삼척 12km²
    # 구역에서 73%). 이제 [[buildings]]가 **건축물대장 주용도**로 주택을
    # 갈라 주므로 그 전제가 사라졌다.
    #
    #   주거 이격   → 배제. 대상이 확인된 주택이고 조문이 직접 금지한다
    #   축사·정온   → 조건부. 조례마다 대상 범위가 달라 단정할 수 없다
    #
    # 둘을 합쳐 배제로 올리면 다시 과대 배제가 된다. 반드시 나눠 쓴다.
    ord_house, ord_other = ordinance_zones(buffers)
    # 건물 이격 버퍼는 반경 원이라 사업구역 링 밖으로 나가기 쉽다. 지도의
    # 조례 이격 윤곽(점선)이 검토 구역 경계 밖까지 그려지던 원인이었다.
    ord_house = geo.clip(ord_house, area) if ord_house is not None else None
    ord_other = geo.clip(ord_other, area) if ord_other is not None else None

    # 도로 이격 — 건축물 레이어로는 만들 수 없어 종전에 통째로 빠져 있던 것이다.
    # 국가교통DB 도로등급으로 국도/지방도(배제)와 시·군도(조건부)를 가른다.
    rule_sets_for_road = {s['code']: _rule_set(s['rules'])
                          for s in slices if s.get('rules')}
    try:
        road_zones, road_notes, road_labels, road_detail = roads.setback_zones(
            area, rule_sets_for_road, slices)
    except Exception:                                           # noqa: BLE001
        logger.exception('도로 이격 버퍼 생성 실패')
        road_zones, road_notes, road_labels, road_detail = (
            {}, ['도로 이격 버퍼를 만들지 못했습니다.'], {}, [])
    buf_notes.extend(road_notes)
    road_block, road_unc = road_zones.get('blocked'), road_zones.get('uncertain')
    if separation_zone is not None:
        road_block = geo.clip(road_block, separation_zone) if road_block else None
        road_unc = geo.clip(road_unc, separation_zone) if road_unc else None
    # 도로 이격 버퍼도 마찬가지로 사업구역 링으로 자른다 — 그러지 않으면
    # 도로가 구역 경계 가까이를 지날 때 버퍼가 경계 밖으로 삐져나온다.
    road_block = geo.clip(road_block, area) if road_block is not None else None
    road_unc = geo.clip(road_unc, area) if road_unc is not None else None

    ord_parts = [g for g in (ord_house, ord_other, road_block, road_unc)
                 if g is not None]

    # 지도에서 면에 커서를 올렸을 때 보여 줄 한 줄. "빨간 면이 왜 배제인가"에
    # 답하지 못하면 지도를 봐도 다음 행동이 서지 않는다.
    overlay_labels: dict = dict(road_labels)
    if ord_house is not None:
        overlay_labels['ordinance_house'] = _house_label(rule_sets_for_road)

    # ⚠️ 경과규정을 **분류 전에** 본다.
    #
    # 발전사업허가일이 조례(경과조치) 시행일보다 앞서면 부칙에 따라 종전
    # 기준이 적용될 수 있다. 그런 사업을 조례 이격으로 배제해 버리면,
    # 실제로는 진행 가능한 사업지가 보고서에서 **사업 불가**로 못 박힌다.
    # (삼척 천봉풍력 — 허가일이 조례 개정 이전이라 진행 중인 사업이다)
    #
    # 그렇다고 빼 주지도 않는다. 부칙이 그 사업에 적용되는지는 관할 지자체가
    # 판단할 문제다. **배제가 아니라 조건부**로 두고 왜 그런지 밝힌다.
    grand = _grandfathering(slices, permit_date)
    grandfathered = bool(grand.get('review_required'))
    ord_status = (Status.CONDITIONAL.value if grandfathered
                  else Status.IMPOSSIBLE.value)
    ord_bucket = conditional_parts if grandfathered else blocked_parts
    tag = ' — 경과규정 검토 대상' if grandfathered else ''

    # 코드가 아니라 지자체 이름으로 적는다. '조례 이격거리 (12850)'은
    # 보고서에서 무엇을 가리키는지 알 수 없다.
    name_of = {s['code']: s['sigungu'] for s in slices}
    for code, got in buffers.items():
        who = name_of.get(code) or code
        for key, label, status, bucket in (
                ('housing', f'주거 이격{tag}', ord_status, ord_bucket),
                ('other', '축사·정온시설 이격', Status.CONDITIONAL.value,
                 conditional_parts)):
            piece = got.get(key)
            if piece is None:
                continue
            bucket.append(piece)
            by_reason.append({
                'layer': f'조례 {label} ({who})',
                'status': status,
                'area_m2': float(piece.area),
                'ratio': float(piece.area) / total,
                # 주거 이격은 조문 근거가 분명해 잠정이 아니다. 다만 실거주·
                # 빈집 여부는 확인할 수 없어 그 사실을 주석으로 남긴다.
                # 경과규정 대상이면 적용 여부 자체가 미정이라 잠정이다.
                'provisional': key != 'housing' or grandfathered,
            })
    # 도로 이격 — 등급이 분명한 국도·지방도는 배제, 시·군도는 조건부다.
    # 경과규정 대상이면 도로도 함께 조건부로 내린다.
    for piece, label, status, bucket in (
            (road_block, f'조례 도로 이격 (국도·지방도){tag}',
             ord_status, ord_bucket),
            (road_unc, '조례 도로 이격 (시·군도 — 군도 여부 확인 필요)',
             Status.CONDITIONAL.value, conditional_parts)):
        if piece is None:
            continue
        bucket.append(piece)
        by_reason.append({
            'layer': label, 'status': status,
            'area_m2': float(piece.area), 'ratio': float(piece.area) / total,
            # 시·군도는 군도인지 가릴 수 없어 잠정이다.
            'provisional': status == Status.CONDITIONAL.value,
        })

    if buffers and grandfathered:
        buf_notes.append(
            '**발전사업허가일이 조례 시행일보다 앞서 조례 이격을 배제가 아니라 '
            '조건부로 집계**했습니다. 부칙 경과조치에 따라 종전 기준이 적용될 수 '
            '있어 무조건 불가로 볼 수 없기 때문입니다. **면제가 확정된 것은 '
            '아닙니다** — 부칙이 이 사업에 적용되는지는 관할 지자체가 판단합니다. '
            '아래 「경과규정 검토」 절의 부칙 원문을 확인하고 지자체와 협의하십시오.')
    elif buffers:
        buf_notes.append(
            '**조례가 정한 주거 이격 범위는 사업 불가(배제)로 집계**했습니다 — '
            '조문이 직접 금지하는 범위이며 대상 주택은 건축물대장 주용도로 '
            '확인한 것입니다. 축사·정온시설 이격은 조례마다 대상 범위가 달라 '
            '조건부로 두었습니다. 다만 조례는 주민등록 실거주 주택만을 대상으로 '
            '하고 빈집을 제외하나 그 정보가 공개되지 않으므로, 배제 면적은 '
            '대장상 주택 기준의 **상한선**입니다. '
            '발전사업허가일을 입력하면 조례 시행일과 대조해 경과규정 검토 대상 '
            '여부를 함께 판정합니다.')

    # 겹침 면 문구 — **실제 사유**로 만든다. by_reason이 다 채워진 뒤라야
    # 무엇이 그 색을 만들었는지 알 수 있어 여기서 짓는다.
    by_reason.sort(key=lambda r: r['area_m2'], reverse=True)
    overlay_labels['blocked'] = _overlay_label(
        by_reason, Status.IMPOSSIBLE.value, '배제', overlay_labels)
    overlay_labels['conditional'] = _overlay_label(
        by_reason, Status.CONDITIONAL.value, '조건부', overlay_labels)

    # area 경계로 자른다. 도로·건물 이격 버퍼(road_block/road_unc,
    # ord_house/ord_other)는 지자체·관할 경계 기준으로 만들어져 사업구역
    # 링 밖으로 삐져나올 수 있다 — 클릭한 경계 밖 땅까지 배제·조건부로
    # 세면 면적 숫자와 제약도가 실제 구역보다 부풀려진다.
    blocked = geo.clip(geo.union(blocked_parts + blanket_blocked), area)
    all_conditional_parts = conditional_parts + blanket_conditional
    conditional = geo.clip(
        geo.subtract(geo.union(all_conditional_parts), blocked), area) \
        if all_conditional_parts else None

    free = geo.subtract(geo.subtract(area, blocked), conditional)
    pending_m2 = jmeta.get('pending_area_m2', 0.0)

    # 조례 개정 전에 발전사업허가를 받은 사업은 부칙 경과조치로 종전 기준이
    # 적용될 수 있다. 적용 여부는 관할 지자체가 판단하므로 **여기서 빼지 않고**,
    # 뺐을 때의 값을 함께 낸다. 이 항목 하나가 결론을 통째로 뒤집기 때문에
    # 언급하지 않으면 보고서가 사실과 크게 다른 결론을 내게 된다.
    # (grand는 위에서 이미 구했다 — 조례 이격을 배제로 볼지 조건부로 볼지가
    #  그 값에 달려 있어 분류보다 먼저 판정해야 했다.)
    if ord_parts:
        ord_union = geo.clip(geo.union(ord_parts), area)
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
        'energy_type': energy_mod.normalize(energy),
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
        # 발전기가 **실제로 서는** 지자체. 위 jurisdictions는 검토 면적
        # (배치선 버퍼) 기준이라 발전기가 하나도 없는 이웃 지자체가 큰 비율로
        # 잡힌다 — 평창 문재풍력에서 8기가 전부 평창인데 검토 면적으로는
        # 횡성군이 47.3%로 나왔다. 인허가는 발전기가 선 자리가 가르므로
        # 두 기준을 나란히 낸다.
        'turbine_jurisdictions': _turbine_jurisdictions(layout, slices),
        # 조회하지 못한 레이어가 있으면 그만큼 제약을 덜 본 것이다.
        # 가용면적이 실제보다 크게 나올 수 있으므로 반드시 함께 읽어야 한다.
        'fetch_failures': failures,
        'notes': buf_notes,
        # 배치선 검토일 때만 채워진다. 폴리곤 검토면 None.
        'layout': layout,
        # 필지 검토일 때만 채워진다 — 필지 목록·지목 구성·합계 면적.
        'parcel': parcel,
        # 조례 경과규정 검토 — 시스템은 판정하지 않고 근거와 시나리오만 제시한다.
        'grandfathering': grand,
        # 주거 이격은 blocked에 이미 녹아 있지만 따로도 싣는다. 제약도에서
        # **무엇 때문에 불가인지** 윤곽으로 짚어 주기 위한 것이다 — 붉은 면만
        # 보면 규제 레이어 때문인지 조례 이격 때문인지 알 수 없다.
        # 조례 이격 **기준표** — 결과보다 기준을 먼저 보여야 읽힌다.
        'setback_rules': setback_table(slices),
        'geoms': {'area': area, 'blocked': blocked,
                  'conditional': conditional, 'free': free,
                  'ordinance_house': ord_house,
                  'ordinance_road': road_block,
                  # 경과규정 대상이면 제약도 범례를 '사업 불가'가 아니라
                  # '경과규정 검토 대상'으로 적어야 한다. 색만 바꾸고 글은
                  # 그대로 두면 지도가 표와 어긋난다.
                  'ordinance_grandfathered': grandfathered,
                  'overlay_labels': overlay_labels,
                  # 도로별 선·이격 범위 — 어느 도로로부터 얼마만큼 침범되는지
                  'road_detail': road_detail,
                  'ordinance_road_uncertain': road_unc,
                  # 항목별 환경성 평가 지도(용도지역·농업진흥·생태자연도 등)가
                  # 쓴다. {레이어 제목: 도형}.
                  'layer_geoms': layer_geoms},
    }


# ======================================================================
# 호기별 지점 검토
# ======================================================================
def evaluate_points(points: list, radius_m: int, capacity_mw=None,
                    label: str = '호기', job_id: str = '',
                    energy: str = energy_mod.DEFAULT,
                    rings: list | None = None,
                    geoms: list | None = None,
                    usable_m2: float | None = None) -> list[dict]:
    """
    지점마다 기존 62개 항목 검토를 돌린다.

    구역 제약도는 '면적이 어떻게 나뉘는가'에 답하지만, 규제 항목별 가부는
    지점에서만 성립한다. 배치선 검토에서 그 둘이 다 필요하다 — 어느 호기가
    무엇에 걸리는지 알아야 배치를 고칠 수 있기 때문이다.

    지점 간은 **순차**로 돈다. 각 지점 내부가 이미 스레드풀이라, 지점까지
    동시에 돌리면 외부 API에 과도한 동시요청이 간다(engine.compare와 같은 이유).

    rings를 주면 지점 i의 검토 대상이 rings[i] 도형이 된다(필지 경계).
    없으면 점+반경이다. 경사도처럼 면에서만 뜻이 서는 항목이 실제 부지를
    보게 하려면 이 값이 필요하다 — 반경 원을 재면 옆 땅의 경사가 섞인다.

    반환 [{'no','lat','lng','address','sido','sigungu','result'}]
    """
    from .engine import _Cancelled as _EngineCancelled, evaluate
    from .geocode import reverse_geocode

    out = []
    for i, (lat, lng) in enumerate(points, start=1):
        # 호기 하나가 통째로 62개 조회다. 시작 전에 확인하면 남은 호기를
        # 통째로 아낄 수 있다.
        jobs.check(job_id)
        jobs.set_progress(job_id, i - 1, len(points), f'{i}/{len(points)}{label} 규제 검토')
        addr = sido = sigungu = ''
        try:
            g = reverse_geocode(lat, lng) or {}
            addr = g.get('address') or g.get('road_address') or ''
            sido, sigungu = g.get('sido', ''), g.get('sigungu', '')
        except Exception:                                       # noqa: BLE001
            logger.warning('%d%s 역지오코딩 실패', i, label)
        try:
            res = evaluate(lat=lat, lng=lng, radius_m=radius_m, address=addr,
                           capacity_mw=capacity_mw, sido=sido, sigungu=sigungu,
                           energy=energy,
                           area_ring=(rings[i - 1] if rings and i - 1 < len(rings)
                                      else None),
                           area_geom=(geoms[i - 1] if geoms and i - 1 < len(geoms)
                                      else None),
                           usable_m2=usable_m2,
                           should_cancel=(lambda: jobs.is_cancelled(job_id))
                           if job_id else None)
        except _EngineCancelled as exc:
            raise jobs.Cancelled(job_id) from exc
        except Exception as e:                                  # noqa: BLE001
            logger.exception('%d%s 검토 실패', i, label)
            out.append({'no': i, 'lat': lat, 'lng': lng, 'address': addr,
                        'sido': sido, 'sigungu': sigungu, 'result': None,
                        'error': type(e).__name__})
            continue
        out.append({'no': i, 'lat': lat, 'lng': lng, 'address': addr,
                    'sido': sido, 'sigungu': sigungu, 'result': res})
        jobs.set_progress(job_id, i, len(points), f'{i}/{len(points)}{label} 완료')
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
            new = it.item_name not in merged
            row = merged.setdefault(it.item_name, {
                'category': it.category, 'item_name': it.item_name,
                'status': 'POSSIBLE', 'worst_no': None, 'reason': '',
                'law': it.law, 'article': it.article,
                # 조건부·확인 필요 항목을 어디서 어떻게 확인하는지 보고서에
                # 싣기 위해 함께 들고 간다. 판정만 있고 다음 행동이 없으면
                # 읽는 사람이 각 기관을 다시 찾아야 한다.
                'action_required': it.action_required, 'source_url': it.source_url,
                'data_source': it.data_source, 'difficulty': it.difficulty.value,
                'unknown_reason': '', 'per_point': {},
            })
            s = it.status.value
            row['per_point'][ev['no']] = s
            # 첫 항목의 사유는 무조건 채운다. 종전에는 '더 나쁜 값'일 때만
            # 채워, 모든 호기가 같은 판정이면(특히 해당없음) 주요 결과 칸이
            # 통째로 비어 나갔다.
            if new or _SEVERITY[s] > _SEVERITY[row['status']]:
                row.update(status=s, worst_no=ev['no'], reason=it.reason,
                           unknown_reason=it.unknown_reason,
                           action_required=it.action_required,
                           source_url=it.source_url, difficulty=it.difficulty.value)
    rows = list(merged.values())
    for r in rows:
        nos = sorted(no for no, v in r['per_point'].items()
                     if _SEVERITY[v] >= _SEVERITY[r['status']] > 0)
        r['hit_nos'] = nos
        r['hits'] = len(nos)
        r['total'] = len(r['per_point'])
        r['hit_label'] = nos_label(nos, r['total'])
    rows.sort(key=lambda r: (-_SEVERITY[r['status']], r['category'], r['item_name']))
    return rows


def nos_label(nos: list[int], total: int = 0) -> str:
    """
    걸린 호기 번호를 사람이 읽는 형태로.

    '10/10기'는 몇 기인지만 알려줄 뿐 어느 기를 옮겨야 하는지는 말해주지
    않는다. 배치를 고치려면 번호가 필요하다.

        [1..10] (총 10) → '1~10호기 (전 호기)'
        [1,3,4,5,9]     → '1·3~5·9호기 (5기)'
        [7]             → '7호기'

    total을 주지 않으면 개수를 붙이지 않는다. 이미 '공통 제약'처럼 문맥이
    개수를 말하고 있는 자리에서 '(2기)'가 겹쳐 붙는 것을 피하기 위해서다.
    """
    if not nos:
        return '-'
    runs: list[tuple[int, int]] = []
    for n in nos:
        if runs and n == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], n)
        else:
            runs.append((n, n))
    # 3연속 이상만 물결로 묶는다. '1~2'는 '1·2'보다 읽기 나쁘다.
    body = '·'.join(f'{a}~{b}' if b - a >= 2 else '·'.join(str(x) for x in range(a, b + 1))
                    for a, b in runs)
    if not total or len(nos) == 1:
        return f'{body}호기'
    if len(nos) == total > 1:
        return f'{body}호기 (전 호기)'
    return f'{body}호기 ({len(nos)}기)'


def _house_label(rule_sets: dict) -> str:
    """
    주거 이격 툴팁 한 줄. 조례가 호수로 구간을 나누면 그대로 적는다.

    '조례 주거 이격'만 적으면 왜 이만큼 빠지는지 알 수 없다. **몇 호 기준으로
    몇 m인지**가 있어야 배치를 어떻게 고칠지 판단할 수 있다.
    """
    n = max((rs.get('house_n', 0) for rs in rule_sets.values()), default=0)
    ge = max((rs.get('house_ge', 0) for rs in rule_sets.values()), default=0)
    lt = max((rs.get('house_lt', 0) for rs in rule_sets.values()), default=0)
    if n and ge and lt and ge != lt:
        return (f'조례 주거 이격 {n}호 이상 주거지역 {ge:,}m · '
                f'{n}호 미만 {lt:,}m')
    d = ge or lt
    return f'조례 주거 이격 주거지역에서 {d:,}m' if d else '조례 주거 이격'


#: 제약 사유에 덧붙일 부연. 이름만으로는 다음 행동이 서지 않는 항목이다.
#:
#: '농업진흥지역'이 대표다 — 이름만 보면 불가로 읽히지만, 당사 사업 경로는
#: 전용이 아니라 **염도평가 → 농지 타용도 일시사용허가**다. 그 사실을 지도에서
#: 바로 알 수 있어야 후보를 버리지 않는다.
OVERLAY_ANNOTATION = (
    ('농업진흥', '염도 {saline}dS/m 이상이면 농지 타용도 일시사용허가로 '
                 '태양광 사업 가능'),
    ('농림지역', '염도 {saline}dS/m 이상이면 농지 타용도 일시사용허가로 '
                 '태양광 사업 가능'),
    ('생태자연도', '1등급은 원칙적 회피 · 저감대책 협의 대상'),
    ('산지', '산지전용·일시사용 허가 협의 대상'),
)
#: 겹침 문구에 늘어놓을 사유 수.
#:
#: **하나만 적는다.** 셋을 늘어놓으면 툴팁이 서너 줄로 불어나 지도를 덮고,
#: 정작 무엇이 결정적인지 읽히지 않는다. 면적이 가장 넓은 사유가 그 색을
#: 만든 주범이므로 그것만 보인다. 나머지는 필지를 눌러 보면 전부 나온다.
OVERLAY_REASON_MAX = 1


def _overlay_label(by_reason: list, status: str, head: str,
                   detail: dict) -> str:
    """
    겹침 면에 커서를 올렸을 때 보여 줄 한 줄.

    "배제 — 불가 판정 레이어 또는 조례 이격 범위" 같은 일반론은 지도를 봐도
    다음 행동이 서지 않는다. **무엇 때문에 그 색인지**를 넓은 것부터 적고,
    조례 이격처럼 거리·대상이 있는 항목은 그 값까지 붙인다.
    """
    from .providers.solar_site import SALINE_DSM

    rows = sorted((r for r in by_reason if r['status'] == status),
                  key=lambda r: -r['area_m2'])[:OVERLAY_REASON_MAX]
    if not rows:
        return head

    # 사유 이름 → 미리 만들어 둔 자세한 문구. 조례 이격은 거리·도로 이름이
    # 이미 붙어 있어 그쪽이 훨씬 쓸모 있다. 이름 조각으로 잇는다.
    fine = {
        '도로 이격 (국도': detail.get('ordinance_road', ''),
        '도로 이격 (시·군도': detail.get('ordinance_road_uncertain', ''),
        '주거 이격': detail.get('ordinance_house', ''),
    }

    parts = []
    for r in rows:
        name = r['layer']
        hit = next((v for key, v in fine.items() if v and key in name), '')
        if hit:
            parts.append(hit)
            continue
        note = next((t for key, t in OVERLAY_ANNOTATION if key in name), '')
        parts.append(f'{name}({note.format(saline=SALINE_DSM)})' if note else name)
    return f'{head} — ' + ' · '.join(parts)


#: 이격 기준표에 실을 항목 — (열쇠, 구분 이름). 차례가 곧 표의 차례다.
#: 조례가 정하지 않은 항목은 표에서 빠진다 — 없는 기준을 만들어 적지 않는다.
SETBACK_ROWS = (
    ('road_major', '고속도로 및 국도'),
    ('road_minor', '지방도 및 군도'),
    ('house_ge', '{n}호 이상 주거지역'),
    ('house_lt', '{n}호 미만 주거지역'),
    ('livestock', '축사·가축시설'),
    ('quiet', '정온시설'),
)


def setback_table(slices: list) -> list[dict]:
    """
    조례 이격 **기준표**. → [{sigungu, target, distance_m}]

    지금까지 이격 기준은 판정 사유 문장 안에 묻혀 있었다. 보고서를 넘겨보는
    사람이 "이 지자체는 무엇에서 몇 m인가"를 알려면 문단을 다 읽어야 했다.
    인허가 실무 검토서가 예외 없이 이 표를 첫 장에 두는 데는 까닭이 있다 —
    **기준을 먼저 보여야 결과가 읽힌다.**
    """
    out: list[dict] = []
    for s in slices:
        if not s.get('rules'):
            continue
        rs = _rule_set(s['rules'])
        n = rs.get('house_n', 0)
        for key, label in SETBACK_ROWS:
            d = rs.get(key)
            if not d:
                continue
            if '{n}' in label:
                if not n:
                    label = '주거지역'
                else:
                    label = label.format(n=n)
            out.append({'sigungu': s['sigungu'], 'target': label,
                        'distance_m': int(d)})
        # 호수 산정 방식은 거리가 아니지만 **결과를 가르는 기준**이다.
        # 같은 마을이 '10호 이상'이 되느냐 마느냐가 500m와 300m를 가른다.
        if rs.get('house_radius') and n:
            out.append({'sigungu': s['sigungu'],
                        'target': f'※ {n}호 산정 반경(이격거리 아님)',
                        'distance_m': int(rs['house_radius'])})
    return out


#: 환경성 항목 지도로 낼 최대 개수. 트리거된 규제가 많아도 화면·보고서가
#: 끝없이 늘어나지 않게 넓은 사유부터 자른다.
ENV_MAP_MAX = 4

#: 조례·도로 이격은 이미 요약지도·「지자체 조례」 절에 실제 지도·표가 있다.
#: 여기서 또 항목으로 내면 같은 그림이 두 번 나온다.
ENV_MAP_SKIP_PREFIX = '조례 '


def env_layers(result: dict) -> list[dict]:
    """
    환경성 평가 협의지침에 낼 항목 목록 — 화면 미리보기와 보고서 캡처가
    **같은 목록**을 써야 화면에서 고른 항목과 보고서에 실리는 항목이
    어긋나지 않는다.

    지도로 낼 수 있는 것은 구역 전체를 대상으로 조회한 규제 레이어뿐이다
    (용도지역·농업진흥지역·자연공원·보호구역 등 — `layer_geoms`에 원본
    도형이 남아 있다). 생태자연도·철새도래지·산사태위험등급처럼 지점(반경)
    기준으로만 조회하는 항목은 구역 전체 도형이 없어 여기 들어가지 않는다.

    → [{'kind': 'zoning'|'item', 'name': str, 'status': str|None,
        'area_m2': float, 'geom': shapely}]

    kind='zoning'인 항목은 모두 합쳐 **하나의 "용도지역 구성" 지도**로 낸다
    (국토계획법 4종 분류를 한 지도에서 봐야 뜻이 선다). kind='item'은
    항목마다 **따로** 지도 하나씩 낸다.
    """
    g = result.get('geoms') or {}
    layer_geoms = g.get('layer_geoms') or {}
    if not layer_geoms:
        return []

    out: list[dict] = []
    for z in result.get('zoning') or []:
        geom = layer_geoms.get(z['layer'])
        if geom is not None:
            out.append({'kind': 'zoning', 'name': z['layer'], 'status': None,
                       'area_m2': z['area_m2'], 'geom': geom})

    # 면적 분할 항목(by_reason)과 **구역 전체 조건(blanket)** 을 함께 낸다.
    #
    # 농업진흥지역도처럼 사업구역(필지들)을 100% 덮는 레이어는 blanket으로
    # 빠져 by_reason에 없다. 그런데 그 항목이야말로 협의의 중심이다 —
    # 여기서 빼면 ② 농지법 카드와 ③ 환경성 면에서 농업진흥지역 지도가
    # 통째로 사라진다(실측: 사업구역을 필지 기반으로 정제하자 커버리지가
    # 99.0%→100%가 되며 그렇게 됐다).
    reasons = [r for r in ((result.get('by_reason') or [])
                           + (result.get('blanket') or []))
              if not r['layer'].startswith(ENV_MAP_SKIP_PREFIX)
              and layer_geoms.get(r['layer'])]
    for r in reasons[:ENV_MAP_MAX]:
        out.append({'kind': 'item', 'name': r['layer'], 'status': r['status'],
                   'area_m2': r['area_m2'], 'geom': layer_geoms[r['layer']]})
    return out
