"""
도로 이격 — 조례가 정한 도로 등급별 버퍼
---------------------------------------------------------------
지자체 조례는 발전시설을 **도로에서 몇 m 떨어뜨릴지**를 등급으로 나눠 정한다.

    장흥군 관리계획 조례 제20조의2 제1호
    "고속도로와 국도에서는 1천미터, 지방도와 군도에서는 500미터 안에
     입지하지 아니할 것"

종전에는 이 조항이 통째로 빠져 있었다. 이격 버퍼를 만들 수 있는 레이어가
도로명주소건물 하나뿐이라 **도로에서는 버퍼를 만들 방법이 없었기** 때문이다
(`available._rule_set`이 이름만 남기고 버렸다). 그 결과 장흥 후보지에서
가용면적이 실제보다 40 ha 넓게 나왔다.

■ 왜 국가교통DB인가

도로명주소도로(`lt_l_sprd`)에는 **도로명만** 있고 등급이 없다(실측: 속성이
`rn` 하나뿐). 조례가 요구하는 국도/지방도/군도 구분을 할 수 없다.

국가교통DB 표준링크(`lt_l_moctlink`)에는 `rd_rank_h`가 있고, 값이 **조례
용어와 그대로 일치**한다 — `일반국도`·`지방도`·`시·군도`.

■ ⚠️ '시·군도'를 그대로 배제하면 안 된다

조례가 규율하는 것은 **군도**(군수가 노선을 지정·고시한 법정도로)다. 그런데
국가교통DB의 `시·군도`에는 그것과 농어촌도로·리도가 뭉쳐 있다. 장흥 후보지
에서 실측하면 이렇게 갈린다.

    지방도만 적용            남는 면적 172.4 ha   ← 전문업체 165 ha와 근접
    시·군도까지 전부 적용     남는 면적 106.2 ha   ← 과대 배제

덕산신상길 하나만 넣어도 107.8 ha로 무너진다. 그래서 **시·군도는 배제가
아니라 조건부**로 두고, 걸린 도로 이름을 보고서에 실어 사용자가 지자체에
군도 노선 여부를 확인하게 한다. 모르는 것을 금지로도 허용으로도 단정하지
않는 것이 이 시스템의 원칙이다.
"""
from __future__ import annotations

import logging

from . import geo
from .providers.vworld import VworldClient

logger = logging.getLogger(__name__)

#: 국가교통DB 표준링크. `rd_rank_h`에 도로 등급이 들어 있다.
ROAD_LAYER = 'lt_l_moctlink'

#: 도로 등급 → 조례 항목. `available._rule_set`이 내는 열쇠와 맞춘다.
#:   road_major  고속도로·국도
#:   road_minor  지방도·군도
RANK_TO_KEY = {
    '고속국도': 'road_major',
    '일반국도': 'road_major',
    '특별·광역시도': 'road_minor',
    '특별광역시도': 'road_minor',
    '지방도': 'road_minor',
    '시·군도': 'road_minor',
    '시군도': 'road_minor',
}

#: 이 등급은 **배제로 단정하지 않는다.** 조례 대상인 군도와 대상이 아닐 수
#: 있는 농어촌도로가 한 값에 뭉쳐 있기 때문이다(모듈 머리말 참고).
UNCERTAIN_RANKS = {'시·군도', '시군도'}

#: 도로를 구역 밖 어디까지 모을지. 가장 큰 이격거리보다 넉넉해야 경계 바로
#: 밖 도로가 누락되지 않는다.
MARGIN_M = 300


def setback_zones(area_geom, rule_sets: dict,
                  slices: list) -> tuple[dict, list[str], dict, list]:
    """
    조례 도로 이격 버퍼. → (도형, 안내문, 지도 툴팁 문구)

    도형은 {'blocked': geom, 'uncertain': geom}

    `rule_sets`는 지자체 코드 → `available._rule_set()` 결과다. 조각마다
    조례가 다르므로 거리도 조각별로 적용하고, 도로는 행정구역을 가리지 않고
    모은다 — 조문이 관할구역으로 한정하지 않는 것이 일반적이라 경계 너머
    도로도 이격 대상이다.

    ■ 반환을 둘로 나누는 까닭
      `blocked`   국도·지방도 — 등급이 분명해 배제로 센다
      `uncertain` 시·군도 — 군도인지 농어촌도로인지 자료로 가릴 수 없다
    """
    dists = [d for rs in rule_sets.values()
             for d in (rs.get('road_major'), rs.get('road_minor')) if d]
    if not dists:
        return {}, [], {}, []

    search = area_geom.buffer(max(dists) + MARGIN_M)
    try:
        feats, meta = VworldClient.fetch_area(ROAD_LAYER, search)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('도로 조회 실패: %s', exc)
        return {}, [f'도로 레이어를 조회하지 못해 **조례 도로 이격이 반영되지 '
                    f'않았습니다** ({type(exc).__name__}). 가용면적이 실제보다 '
                    f'넓게 나올 수 있습니다.'], {}, []
    if meta.get('strategy') == 'failed':
        return {}, ['도로 레이어를 조회하지 못해 **조례 도로 이격이 반영되지 '
                    '않았습니다.** 가용면적이 실제보다 넓게 나올 수 있습니다.'], {}, []

    blocked_parts, uncertain_parts = [], []
    names: dict[str, set] = {}
    # 도로 이름별로 **선과 이격 범위를 따로** 모은다. 합쳐 버리면 지도에서
    # "어느 도로로부터 얼마만큼 침범됐는가"를 볼 수 없다 — 붉은 면 하나만
    # 남아 어느 도로가 원인인지 알 수 없다.
    per_road: dict[tuple, dict] = {}
    for s in slices:
        rs = rule_sets.get(s['code'])
        if not rs:
            continue
        for f in feats:
            p = f.get('properties') or {}
            rank = (p.get('rd_rank_h') or '').strip()
            key = RANK_TO_KEY.get(rank)
            d = rs.get(key) if key else None
            if not d:
                continue
            g = geo.geom_from_geojson(f.get('geometry'))
            if g is None:
                continue
            try:
                line = geo.to_metric(g)
                buf = line.buffer(d)
            except Exception:                                   # noqa: BLE001
                continue
            road_nm = (p.get('road_name') or '이름 미상').strip()
            slot = per_road.setdefault((road_nm, rank, d),
                                       {'lines': [], 'bufs': []})
            slot['lines'].append(line)
            slot['bufs'].append(buf)
            # 조례가 도로 등급을 가리지 않거나 농어촌도로까지 들면 '시·군도'가
            # 통째로 대상이라 조건부로 둘 까닭이 없다(삼척 '도로 1,000m',
            # 완도 '국도·지방도·군도·농어촌도로 1,000m').
            unsure = (rank in UNCERTAIN_RANKS
                      and not rs.get('road_minor_certain'))
            (uncertain_parts if unsure else blocked_parts).append(buf)
            names.setdefault(rank, set()).add(
                (p.get('road_name') or '이름 미상').strip())

    out: dict = {}
    for name, parts in (('blocked', blocked_parts), ('uncertain', uncertain_parts)):
        piece = geo.clip(geo.union(parts), area_geom) if parts else None
        if piece is not None and not piece.is_empty:
            out[name] = piece
    # 조건부는 배제와 겹치는 부분을 뺀다 — 같은 땅이 두 번 세어지지 않게.
    if out.get('uncertain') is not None and out.get('blocked') is not None:
        rest = geo.subtract(out['uncertain'], out['blocked'])
        if rest is None or rest.is_empty:
            out.pop('uncertain')
        else:
            out['uncertain'] = rest

    return (out, _notes(names, rule_sets, out), _labels(names, rule_sets, out),
            _per_road(per_road, area_geom, rule_sets))


def _notes(names: dict, rule_sets: dict, zones: dict) -> list[str]:
    """
    무엇을 어떤 거리로 적용했는지 밝힌다. 숫자만 남으면 근거를 못 되짚는다.

    ⚠️ **실제로 구역에 닿았는지까지 말한다.** 조례에 도로 조항이 있고 주변에
       도로가 있어도, 이격 반경이 구역에 미치지 못하면 빠지는 면적은 없다
       (삼척 천봉풍력 — 능선이라 1km 안에 도로가 없다). 그런데도 '적용했다'고만
       적으면 무언가 빠진 줄 알고 면적이 맞지 않는다고 여기게 된다.
    """
    if not names:
        return []
    if not zones:
        applied = ' · '.join(
            f'{rank} {max((rs.get(RANK_TO_KEY.get(rank), 0) for rs in rule_sets.values()), default=0):,}m'
            for rank in sorted(names))
        return [f'조례 도로 이격({applied})을 검토했으나 **구역에 닿는 범위가 '
                f'없습니다** — 주변 도로가 이격거리보다 멀리 있어 빠지는 면적이 '
                f'없습니다. 조항을 놓친 것이 아닙니다.']
    dist = {}
    for rs in rule_sets.values():
        for k in ('road_major', 'road_minor'):
            if rs.get(k):
                dist[k] = max(dist.get(k, 0), rs[k])

    out = []
    applied = []
    for rank, ns in sorted(names.items()):
        key = RANK_TO_KEY.get(rank)
        applied.append(f'{rank} {dist.get(key, 0):,}m ({len(ns)}개 노선)')
    out.append('조례 도로 이격을 국가교통DB 도로등급으로 적용했습니다 — '
               + ' · '.join(applied) + '.')

    unc = {r: ns for r, ns in names.items() if r in UNCERTAIN_RANKS}
    if not unc:
        return out

    # 조례가 등급을 가리지 않거나 농어촌도로까지 들면 시·군도가 통째로 대상이라
    # 배제로 넣는다. 그때 '조건부로 두었다'고 적으면 표와 문구가 어긋난다.
    certain = any(rs.get('road_minor_certain') for rs in rule_sets.values())
    roads = sorted({n for ns in unc.values() for n in ns if n and n != '-'})
    listed = (', '.join(roads[:12])
              + (f' 외 {len(roads) - 12}개' if len(roads) > 12 else ''))
    if certain:
        out.append(
            '조례가 도로 등급을 가리지 않으므로 **시·군도(군도·농어촌도로 포함)도 '
            '배제**로 집계했습니다. 해당 도로: ' + listed + '.')
    else:
        out.append(
            '⚠️ **시·군도는 배제가 아니라 조건부로 두었습니다.** 조례가 규율하는 '
            '것은 군수가 노선을 지정·고시한 **군도**인데, 국가교통DB의 「시·군도」'
            '에는 그것과 농어촌도로·리도가 함께 들어 있어 자료만으로는 가릴 수 '
            '없습니다. 해당 도로: ' + listed
            + '. **관할 지자체에 군도 노선 여부를 확인하십시오** — 군도로 확인되면 '
              '그 범위는 사업 불가가 됩니다.')
        alley = [n for n in roads if _is_alley(n)]
        if alley:
            out.append(
                '※ 위 시·군도 가운데 ' + ', '.join(alley) + '은(는) 도로명이 '
                '「길」로 끝납니다. 도로명주소법 시행령 [별표 1]은 폭 12m 이상 '
                '또는 왕복 2차로 이상인 도로에만 「로」를 붙이므로, 「길」은 그보다 '
                '좁은 도로입니다. 군도는 통상 「로」로 부여되어 **군도가 아닐 '
                '가능성이 높으나 단정할 수는 없습니다** — 군도 지정 여부는 '
                '도로명이 아니라 관할 지자체의 노선 고시로 정해집니다.')
    return out


#: 도로명이 「길」로 끝나는가 — 도로명주소법 시행령 [별표 1]의 도로 유형.
#:
#: 대로(폭 40m 이상/왕복 8차로 이상) · 로(폭 12~40m/왕복 2~8차로) · 길(그 외).
#: 즉 「길」은 **폭 12m 미만이고 왕복 2차로 미만**이다. 군도는 군청↔읍·면
#: 소재지를 잇는 간선 기능이라 통상 「로」가 붙는다. 확정 근거는 아니지만,
#: 어느 노선부터 확인해야 하는지 순서를 정해 준다.
def _is_alley(name: str) -> bool:
    return bool(name) and name.endswith('길')


def _labels(names: dict, rule_sets: dict, zones: dict) -> dict:
    """
    지도에서 면에 커서를 올렸을 때 보여 줄 한 줄.

    "빨간 면이 왜 배제인가"에 답하지 못하면 지도를 봐도 다음 행동이 서지
    않는다. **어느 등급 도로에서 몇 m인지, 그 도로가 무엇인지**까지 적는다.
    """
    def dist(rank: str) -> int:
        key = RANK_TO_KEY.get(rank)
        return max((rs.get(key, 0) for rs in rule_sets.values()), default=0)

    # 줄바꿈 문자로 나눈다 — 기준과 대상 도로를 한 줄에 이어 쓰면 길어져
    # 읽히지 않는다. 화면이 <br>로 바꿔 그린다.
    out: dict = {}
    if zones.get('blocked'):
        parts = [f'{_rank_word(rank)} {dist(rank):,}m'
                 for rank in sorted(names) if rank not in UNCERTAIN_RANKS]
        roads = sorted({n for rank, ns in names.items()
                        if rank not in UNCERTAIN_RANKS for n in ns
                        if n and n != '-'})
        out['ordinance_road'] = (
            ' '.join(parts) + ' 이격거리 조례' + _road_line(roads))
    if zones.get('uncertain'):
        roads = sorted({n for rank, ns in names.items()
                        if rank in UNCERTAIN_RANKS for n in ns
                        if n and n != '-'})
        out['ordinance_road_uncertain'] = (
            f'시·군도 {dist("시·군도"):,}m 이격거리 조례'
            + _road_line(roads) + '\n※ 군도 노선 여부 확인 필요')
    return out


#: 툴팁 한 줄에 늘어놓을 도로 이름 수
ROAD_NAME_MAX = 6


def _road_line(roads: list) -> str:
    """대상 도로 이름을 **다음 줄**에 괄호로 적는다. 없으면 빈 문자열."""
    if not roads:
        return ''
    more = f' 외 {len(roads) - ROAD_NAME_MAX}개' if len(roads) > ROAD_NAME_MAX else ''
    return '\n(' + ', '.join(roads[:ROAD_NAME_MAX]) + more + ')'


def _rank_word(rank: str) -> str:
    """'일반국도' → '국도'. 툴팁은 짧을수록 읽힌다."""
    return {'일반국도': '국도', '고속국도': '고속도로',
            '특별·광역시도': '시도', '시·군도': '시·군도'}.get(rank, rank)


#: 지도에 그릴 도로 수 상한. 촘촘한 지역은 수십 개가 나와 화면을 덮는다.
#: 침범 면적이 큰 것부터 남긴다 — 사업에 실제로 걸리는 도로가 먼저다.
DRAW_ROAD_MAX = 12


def _per_road(per_road: dict, area_geom, rule_sets: dict) -> list[dict]:
    """
    도로별 선과 이격 범위. → [{name, rank, distance_m, blocked, area_m2,
                              line: [[lat,lng]…], rings: [[[lat,lng]…]…]}]

    **어느 도로로부터 얼마만큼 사업지가 침범되는가**에 답하기 위한 것이다.
    합쳐 놓은 붉은 면 하나로는 그 답이 나오지 않는다 — 도로를 옮길 수는
    없으니, 어느 도로가 원인인지 알아야 배치를 어디로 물릴지 정해진다.

    구역에 **실제로 닿는** 도로만 남긴다. 이격 반경이 미치지 못하는 도로를
    그리면 지도가 무의미하게 붐빈다.
    """
    certain = any(rs.get('road_minor_certain') for rs in rule_sets.values())
    out: list[dict] = []
    for (name, rank, dist), slot in per_road.items():
        try:
            hit = geo.clip(geo.union(slot['bufs']), area_geom)
        except Exception:                                       # noqa: BLE001
            continue
        if hit is None or hit.is_empty:
            continue                      # 이격 범위가 구역에 닿지 않는다
        # 도로 선은 구역 + 이격거리만큼만 남긴다. 전 구간을 그리면 화면 밖까지
        # 뻗어 지도가 어지럽다.
        try:
            line = geo.clip(geo.union(slot['lines']), area_geom.buffer(dist))
        except Exception:                                       # noqa: BLE001
            line = None
        out.append({
            'name': name,
            'rank': rank,
            'distance_m': dist,
            # 시·군도는 조례가 등급을 가리지 않을 때만 배제다.
            'blocked': not (rank in UNCERTAIN_RANKS and not certain),
            'area_m2': round(float(hit.area), 1),
            'rings': geo.rings_4326(hit, precision=5),
            'line': geo.lines_4326(line, precision=5) if line is not None else [],
        })
    out.sort(key=lambda r: -r['area_m2'])
    return out[:DRAW_ROAD_MAX]
