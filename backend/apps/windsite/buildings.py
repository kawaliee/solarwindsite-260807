"""
건물 용도 분류 · 주거밀집 군집 판정
---------------------------------------------------------------
이격거리 조례는 '주택'과 '축사'와 '정온시설'을 각각 다른 거리로 규율하고,
부속 건축물은 명시적으로 제외한다. 그런데 V-World 건물 레이어(lt_c_spbd)에는
용도 속성이 없어 창고 한 채와 마을이 구분되지 않았다. 그 결과 이격 버퍼가
검토 구역의 97.8%를 덮는 일이 벌어졌다.

이 모듈은 건축물대장에서 주용도를 가져와 그 구분을 복원한다.

■ 조례가 정한 것을 그대로 옮긴다 (삼척시 도시계획 조례 정의 조항)

  "주거밀집지역"이란 **5호 이상의 주택**(건축법 시행령 별표1 제1호 단독주택 및
  제2호 공동주택)이 형성된 지역을 말한다.
  ※ 호의 산정 방법 — 주민등록이 되어 있는 주민이 실제로 거주하는 **주택 간
  직선거리 50미터 이내**(건물 외벽 기준)에 위치하여 서로 인접한 주택의 합.
  빈집은 주택으로 산정하지 아니하며, **각각의 부속 건축물은 제외**한다.

  → 군집 거리(50m)도 호수(5호)도 우리가 정한 값이 아니다. 조문에서 온다.

■ 자동으로 끝까지 갈 수 없는 부분

  · **주민등록 실거주 여부**와 **빈집 여부**는 공개 데이터가 없다.
    따라서 산출값은 '주택으로 등재된 건물 기준의 상한선'이지 확정치가 아니다.
  · 건축물대장에 등재되지 않은 건물이 약 13% 있다(실측). 무허가·농막·폐가일
    수도, 실거주 중인 무허가 주택일 수도 있다. 어느 쪽인지 모르므로
    **배제도 제외도 하지 않고 '용도 미확인'으로 따로 센다.**

■ 실측으로 확인한 사항 (2026-08, 삼척 근덕면 궁촌리)

  · 건축물대장은 **법정동 단위** 조회가 된다(궁촌리 458건). 건물 1동씩
    부르는 방식으로는 성립하지 않는다
  · **시군구 코드가 두 체계다.** 건물 관리번호는 구 강원도 42230,
    건축물대장은 강원특별자치도 51230을 쓴다. 구 코드로 조회하면 정상
    응답(NORMAL SERVICE)에 0건이 와서 '건물이 없다'로 오인하기 쉽다
  · 매칭은 도로명주소 81% · 지번 65% · **둘 중 하나 87%**.
    두 키가 서로 다른 건물을 잡아주므로 반드시 함께 써야 한다
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from django.conf import settings

from . import geo, httpcache, pnu
from .providers.base import LayerProvider

logger = logging.getLogger(__name__)

TITLE_OP = 'getBrTitleInfo'
PAGE_SIZE = 100
MAX_PAGES = 30          # 법정동 하나에 3,000건까지. 실측 궁촌리 458건.

#: 조례가 정한 주택 간 인접 거리(m). 삼척시 조례 정의 조항의 값이다.
#: 지자체마다 다를 수 있어 원문 파싱으로 옮겨야 하지만, 현재 수집된 조례
#: 대부분이 이 값을 쓴다. 다른 값이 확인되면 지자체별로 분리한다.
CLUSTER_DISTANCE_M = 50

#: 조례 대상 구분. 건축물대장 주용도명(mainPurpsCdNm)을 조례 항목에 잇는다.
#: 조문에 명시된 것만 잇고, 나머지는 대상 아님으로 둔다 — 조례에 없는 대상을
#: 만들어내지 않기 위해서다.
HOUSING = ('단독주택', '공동주택')
LIVESTOCK = ('동물및식물관련시설',)
QUIET = ('교육연구시설', '종교시설', '관광휴게시설', '노유자시설',
         '의료시설', '수련시설', '문화및집회시설')

CAT_HOUSING = 'HOUSING'         # 주택 — 주거밀집 판정 대상
CAT_LIVESTOCK = 'LIVESTOCK'     # 축사·가축시설
CAT_QUIET = 'QUIET'             # 정온시설
CAT_NOT_TARGET = 'NOT_TARGET'   # 창고·공장 등 조례 대상 아님
CAT_ANNEX = 'ANNEX'             # 부속 건축물 — 조례가 명시적으로 제외
CAT_UNKNOWN = 'UNKNOWN'         # 대장 미등재 — 용도 미확인

_ROAD = re.compile(r'([가-힣A-Za-z0-9]+(?:로|길))\s+(\d+)(?:-(\d+))?')
_BULD_NO = re.compile(r'(\d+)(?:-(\d+))?')


def _road_key(road_name: str, bldg_no) -> str | None:
    m = _BULD_NO.match(str(bldg_no or '').strip())
    if not m or not road_name:
        return None
    return f'{road_name}|{m.group(1)}-{m.group(2) or "0"}'


def _road_key_from_addr(addr: str) -> str | None:
    m = _ROAD.search(addr or '')
    return f'{m.group(1)}|{m.group(2)}-{m.group(3) or "0"}' if m else None


def fetch_dong(sigungu_cd: str, bjdong_cd: str) -> list[dict]:
    """법정동 하나의 건축물대장 표제부 전체. 실패하면 빈 목록."""
    key = getattr(settings, 'BLDG_LEDGER_API_KEY', '')
    base = getattr(settings, 'BLDG_LEDGER_BASE', '')
    if not key or not base:
        return []
    # 대장은 행정구역 개편 전 코드로 적재된 지역이 있다. 확인된 대응표가
    # 있을 때만 바꿔 넘긴다 — 자세한 사연은 windsite/pnu.py 참고.
    # (완도군: 12850으로 물으면 0건, 46890으로 물으면 206건)
    sigungu_cd = pnu.for_ledger(sigungu_cd)

    def call() -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {
                'serviceKey': key, '_type': 'json',
                'numOfRows': str(PAGE_SIZE), 'pageNo': str(page),
                'sigunguCd': sigungu_cd, 'bjdongCd': bjdong_cd,
            }
            res = LayerProvider.get(f'{base.rstrip("/")}/{TITLE_OP}', params, timeout=60.0)
            res.raise_for_status()
            body = ((res.json().get('response') or {}).get('body') or {})
            items = body.get('items') or {}
            rows = items.get('item') if isinstance(items, dict) else None
            if isinstance(rows, dict):
                rows = [rows]
            if not rows:
                break
            out.extend(rows)
            if len(out) >= int(body.get('totalCount') or 0):
                break
        return out

    try:
        return httpcache.get_or_set(
            'bldg_ledger', {'sigungu': sigungu_cd, 'bjdong': bjdong_cd}, call)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('건축물대장 조회 실패 %s/%s: %s', sigungu_cd, bjdong_cd, e)
        return []


def _category(row: dict) -> str:
    # 조례가 "각각의 부속 건축물은 제외"라고 못박았다. 용도보다 먼저 본다.
    if (row.get('mainAtchGbCdNm') or '').strip() == '부속건축물':
        return CAT_ANNEX
    purpose = (row.get('mainPurpsCdNm') or '').strip()
    if purpose in HOUSING:
        return CAT_HOUSING
    if purpose in LIVESTOCK:
        return CAT_LIVESTOCK
    if purpose in QUIET:
        return CAT_QUIET
    return CAT_NOT_TARGET


def _index(rows: list[dict]) -> tuple[dict, dict]:
    """대장 레코드를 도로명 키와 지번 키로 색인한다."""
    by_road: dict[str, dict] = {}
    by_lot: dict[str, dict] = {}
    for r in rows:
        k = _road_key_from_addr(r.get('newPlatPlc') or '')
        if k:
            by_road.setdefault(k, r)
        bun, ji = (r.get('bun') or '0000'), (r.get('ji') or '0000')
        if bun != '0000':
            by_lot.setdefault(bun + ji, r)
    return by_road, by_lot


def classify(feats: list[dict], sigungu_codes: list[str]) -> dict:
    """
    건물 피처를 조례 대상별로 분류한다.

    feats: V-World lt_c_spbd 피처 (geometry 포함)
    sigungu_codes: 행정경계에서 얻은 **신 시군구 코드** 목록.
                   건물 관리번호의 코드는 구 코드라 대장 조회에 쓸 수 없다.

    반환: {카테고리: [shapely Point(5179), …], 'counts': Counter}
    """
    # 법정동별로 묶는다. 대장은 법정동 단위로만 받을 수 있다.
    by_dong: dict[str, list] = {}
    for f in feats:
        sn = ((f.get('properties') or {}).get('bd_mgt_sn') or '')
        if len(sn) < 19:
            continue
        by_dong.setdefault(sn[5:10], []).append(f)

    out: dict[str, list] = {c: [] for c in
                            (CAT_HOUSING, CAT_LIVESTOCK, CAT_QUIET,
                             CAT_NOT_TARGET, CAT_ANNEX, CAT_UNKNOWN)}
    counts: Counter = Counter()

    for bjdong, group in by_dong.items():
        rows: list[dict] = []
        for sg in sigungu_codes:
            # 어느 신 코드에 속하는지 모르므로 결과가 나올 때까지 시도한다.
            # 구코드→신코드 대응표를 코드에 박아두는 것보다 안전하다.
            rows = fetch_dong(sg, bjdong)
            if rows:
                break
        by_road, by_lot = _index(rows)

        for f in group:
            p = f.get('properties') or {}
            sn = p['bd_mgt_sn']
            rec = (by_road.get(_road_key(p.get('rd_nm'), p.get('buld_no')) or '')
                   or by_lot.get(sn[11:15] + sn[15:19]))
            cat = _category(rec) if rec else CAT_UNKNOWN
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            out[cat].append(geo.to_metric(g).centroid)
            counts[cat] += 1

    return {**out, 'counts': counts}


def clusters(points: list, distance_m: int = CLUSTER_DISTANCE_M) -> list[list]:
    """
    서로 distance_m 이내로 이어지는 점들을 한 군집으로 묶는다.

    조례의 '호 산정 방법'이 그대로다 — 주택 간 직선거리 50m 이내에 위치하여
    서로 인접한 주택의 합. A-B가 50m, B-C가 50m면 A-C가 100m라도 한 군집이다
    (조문이 '서로 인접한 주택의 합'이라 연쇄를 인정한다).
    """
    n = len(points)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if points[i].distance(points[j]) <= distance_m:
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b

    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(points[i])
    return list(groups.values())


def dense_split(points: list, radius_m: int, min_count: int) -> tuple[list, list]:
    """
    호수 산정을 **반경 방식**으로 한다. → (밀집 주택, 산재 주택)

    조례마다 '몇 호 이상 주거지역'을 세는 방법이 다르다.

        삼척시   주택 간 직선거리 50m 이내로 이어지는 주택의 합  → clusters()
        장흥군   가장 가까운 가구를 기점으로 **반경 500m 안에** 10호 이상

    뒤엣것을 앞엣것으로 세면 결과가 통째로 달라진다. 실측(장흥 후보지):
    50m 연쇄로 묶으면 마을이 1호·6호·2호짜리 조각으로 쪼개져 **10호 이상
    군집이 부지 근처에 하나도 없게** 되고, 주거 이격이 9.1ha → 0.2ha로
    무너졌다. 조례대로 반경 500m로 세면 그 조각들이 한 주거지역이 된다.

    반환은 점 목록 둘이다. 밀집은 '10호 이상' 거리를, 산재는 '10호 미만'
    거리를 쓴다 — 조문이 두 구간에 다른 이격을 준다.
    """
    if not points or radius_m <= 0 or min_count <= 1:
        return list(points), []
    try:
        from shapely.strtree import STRtree

        tree = STRtree(points)
        dense, sparse = [], []
        for p in points:
            n = len(tree.query(p.buffer(radius_m)))
            (dense if n >= min_count else sparse).append(p)
        return dense, sparse
    except Exception:                                           # noqa: BLE001
        logger.exception('반경 호수 산정 실패 — 전부 밀집으로 둔다')
        # 실패하면 **엄한 쪽**으로 둔다. 산재로 두면 이격이 줄어 사업에
        # 유리하게 틀리는데, 이 시스템이 가장 경계하는 방향이다.
        return list(points), []
