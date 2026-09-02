"""
사업구역의 관할 지자체 분할
---------------------------------------------------------------
대규모 육상풍력은 사업구역이 시·군 경계를 넘는 일이 흔하다. 이격거리 조례는
지자체마다 다르므로(같은 주거밀집지역 기준이 1,000m인 곳과 2,000m인 곳이 있다),
구역 전체에 조례 하나를 적용하면 한쪽은 과대, 다른 쪽은 과소 판정이 된다.

적용 규칙
  · **어느 조례를 쓰는가** — 발전시설이 놓일 위치의 지자체 조례.
    조례는 개발행위허가 기준이고 허가권자가 그 시장·군수이기 때문이다.
  · **무엇을 이격 대상으로 보는가** — 행정구역과 무관하게 전부.
    조문이 "주거밀집지역으로부터 직선거리 N미터"라고만 하고 관할구역으로
    한정하지 않는 것이 일반적이다. 경계 너머 마을도 대상이다.

  → 그래서 버퍼는 구역 전체에 한 번 씌우는 것이 아니라 **지자체 조각마다
     그 지자체의 반경으로 따로** 씌워야 한다. 이 모듈은 그 조각을 만든다.

⚠️ 위 두 번째 규칙은 조례 원문으로 확인해야 확정된다. law.go.kr OPEN API가
   서버 IP 검증에 막혀 있어 아직 대조하지 못했다. 그때까지는 한정 문구가
   없다고 보는 쪽(= 경계 밖 시설도 대상)으로 계산한다. 이쪽이 배제면적을
   더 크게 잡는 보수적 방향이라, 틀렸을 때 사업에 유리하게 오판하지 않는다.
   확정되면 SEPARATION_SCOPE만 바꾸면 된다.

데이터
  V-World **WFS** 엔드포인트의 lt_c_adsigg(시군구경계).
  같은 레이어를 데이터 API(req/data)로는 받을 수 없다 — 'data 파라미터의
  값이 유효한 범위를 넘었습니다'로 거절한다. WFS 1.1.0 + 소문자 타입명만 통한다.
  응답 좌표계가 EPSG:5179라 재투영 없이 그대로 쓴다.
"""
from __future__ import annotations

import logging

from django.conf import settings

from . import geo, httpcache
from .providers.base import LayerProvider

logger = logging.getLogger(__name__)

WFS_URL = 'https://api.vworld.kr/req/wfs'
SIGUNGU_LAYER = 'lt_c_adsigg'
#: 읍면동 경계. 사업지가 **어느 면에 속하는지**를 지도에서 보여 주는 데 쓴다.
#: 시군구는 너무 넓어 부지를 짚는 데 도움이 안 되고, 리(lt_c_adri)는 너무
#: 잘아 화면이 조각난다. 실무에서 사업지를 부를 때 쓰는 단위가 읍·면이다.
EMD_LAYER = 'lt_c_ademd'

#: 이격 대상 시설을 관할구역으로 한정하는지. 'ALL' = 한정하지 않음(현재 가정).
#: 조례 원문 대조 후 확정한다. 위 모듈 주석 참고.
SEPARATION_SCOPE = 'ALL'

#: 한 번에 받을 최대 경계 피처. 시군구는 넓은 구역에서도 수십 건이라 넉넉하다.
MAX_FEATURES = 500

#: 이 비율 미만으로 걸친 지자체는 조각으로 세지 않는다. 경계선 자체의
#: 좌표 오차로 생기는 실오라기 같은 조각까지 '관할 지자체'로 세면
#: 조례를 못 찾았다는 경고만 늘고 판단에 도움이 되지 않는다.
MIN_SLICE_RATIO = 0.0005          # 0.05%


class BoundaryUnavailable(RuntimeError):
    """행정경계를 받지 못했다 — 구역을 나눌 수 없다."""


def _fetch(area_geom) -> list[dict]:
    """구역 bbox와 겹치는 시군구 경계 피처 (EPSG:5179 그대로)."""
    minx, miny, maxx, maxy = area_geom.bounds
    params = {
        'SERVICE': 'WFS',
        'REQUEST': 'GetFeature',
        'VERSION': '1.1.0',
        'KEY': settings.VWORLD_API_KEY,
        'DOMAIN': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
        'TYPENAME': SIGUNGU_LAYER,
        'SRSNAME': geo.METRIC_CRS,
        'BBOX': ','.join(f'{v:.2f}' for v in (minx, miny, maxx, maxy)),
        'OUTPUT': 'application/json',
        'MAXFEATURES': str(MAX_FEATURES),
    }

    def call() -> dict:
        res = LayerProvider.get(WFS_URL, params, timeout=60.0)
        res.raise_for_status()
        ct = (res.headers.get('content-type') or '').lower()
        if 'json' not in ct:
            # 오류는 XML로 온다. 본문을 남겨야 원인을 추적할 수 있다.
            raise BoundaryUnavailable(
                f'행정경계 응답이 JSON이 아닙니다 ({ct}): {res.text[:160]}')
        return res.json()

    payload = httpcache.get_or_set('vworld_wfs', params, call)
    return payload.get('features') or []


def split_full_name(full_nm: str) -> tuple[str, str]:
    """
    'full_nm' → (시·도, 조례 주체 시·군·구).

    광역시의 자치구는 조례 주체가 아니라서가 아니라, 특례시 산하 일반구가
    문제다. '경기도 수원시 장안구'의 도시계획 조례는 수원시가 만든다.
    geocode.split_admin()과 같은 규칙을 쓴다 — 두 경로가 다르게 정규화하면
    같은 지점인데 조례가 조회되기도 하고 안 되기도 한다.
    """
    parts = (full_nm or '').split()
    if not parts:
        return '', ''
    sido = parts[0]
    rest = parts[1:]
    if not rest:
        return sido, sido                      # 세종특별자치시 단층제
    if len(rest) >= 2 and rest[-1].endswith('구'):
        return sido, rest[0]                   # 수원시 장안구 → 수원시
    return sido, rest[-1]


def split(area_geom) -> tuple[list[dict], dict]:
    """
    사업구역을 관할 지자체별 조각으로 나눈다.

    반환: (조각 목록, meta)
      조각 = {code, sido, sigungu, full_name, geom, area_m2, ratio}
      meta['uncovered_m2']  어느 경계에도 안 잡힌 면적.
                            0이 아니면 경계 데이터가 구역을 다 덮지 못한 것이다.
    """
    total = float(area_geom.area)
    feats = _fetch(area_geom)
    if not feats:
        raise BoundaryUnavailable('구역과 겹치는 시군구 경계를 찾지 못했습니다.')

    # 한 지자체가 여러 피처로 쪼개져 온다(삼척시 5건 등). 코드로 묶어 합친다.
    by_code: dict[str, dict] = {}
    for f in feats:
        props = f.get('properties') or {}
        code = (props.get('sig_cd') or '').strip()
        if not code:
            continue
        g = geo.geom_from_geojson(f.get('geometry'))
        if g is None:
            continue
        slot = by_code.setdefault(code, {'full_nm': props.get('full_nm') or '',
                                         'name': props.get('sig_kor_nm') or '',
                                         'parts': []})
        slot['parts'].append(g)

    # 큰 조각부터 확정하고, 이미 배정된 부분을 빼가며 다음 조각을 만든다.
    # 인접 시군구 경계는 같은 선을 공유하지만 좌표가 미세하게 어긋나 있어,
    # 그냥 잘라 담으면 조각들이 몇 m² 겹친다. 면적을 두 번 세는 종류의
    # 오차라 허용오차로 눈감지 않고 구조적으로 없앤다.
    candidates = []
    for code, slot in by_code.items():
        merged = geo.union(slot['parts'])
        piece = geo.clip(merged, area_geom) if merged is not None else None
        if piece is not None:
            candidates.append((float(piece.area), code, slot, piece))
    candidates.sort(key=lambda c: c[0], reverse=True)

    slices: list[dict] = []
    taken = None
    for _, code, slot, piece in candidates:
        piece = geo.subtract(piece, taken) if taken is not None else piece
        if piece is None:
            continue
        a = float(piece.area)
        if a / total < MIN_SLICE_RATIO:
            continue
        taken = geo.union([taken, piece]) if taken is not None else piece
        sido, sigungu = split_full_name(slot['full_nm'])
        slices.append({
            'code': code,
            'sido': sido,
            'sigungu': sigungu or slot['name'],
            'full_name': slot['full_nm'],
            'geom': piece,
            'area_m2': a,
            'ratio': a / total,
        })

    leftover = geo.subtract(area_geom, taken) if taken is not None else area_geom
    uncovered = float(leftover.area) if leftover is not None else 0.0

    return slices, {
        'total_area_m2': total,
        # 어느 시군구에도 안 잡힌 면적. 대개 바다다(시군구 경계는 육지만 덮는다).
        # 경계 데이터 누락일 수도 있으므로 구분하지 않고 그대로 올린다. 작더라도
        # 감추지 않는다 — 조용히 0으로 두면 그만큼 가용면적이 부풀어난다.
        'uncovered_m2': uncovered,
        'uncovered_ratio': uncovered / total if total else 0.0,
        'source': 'V-World WFS lt_c_adsigg',
        'separation_scope': SEPARATION_SCOPE,
    }


def with_ordinances(area_geom, energy: str = None) -> tuple[list[dict], dict]:
    """
    split() 결과에 지자체별 이격거리 조례를 붙인다.

    규정이 비어 있는 이유를 세 갈래로 가른다. 뭉뚱그리면 면적 집계가 틀어진다.

      HAS_RULES  이격 규정 있음        → 그 반경으로 버퍼
      NO_RULE    조례를 확인했고 규정 없음 → **버퍼 없음. 정상 판정이다**
      그 외       확인하지 못함          → 판정 보류

    가운데를 보류로 묶으면, 조례상 제한이 없어 멀쩡히 쓸 수 있는 땅까지
    '모르는 땅'이 되어 가용면적이 실제보다 작게 나온다. 태백시가 그 예다 —
    도시계획 조례 조문 104개와 별표 25건을 전부 판독했고 풍력 이격 조항이
    실제로 없다. 이건 모르는 게 아니라 아는 것이다.

    반대로 확인하지 못한 조각을 가용으로 세면 사업에 유리한 쪽으로 틀리고,
    배제로 세면 멀쩡한 부지를 버린다. 그래서 그것만 따로 센다.
    """
    from . import ordinances

    slices, meta = split(area_geom)
    unverified: list[str] = []
    no_rule: list[str] = []
    for s in slices:
        try:
            rules, state = ordinances.ordinance_state(s['sido'], s['sigungu'], energy)
        except Exception as e:                                  # noqa: BLE001
            logger.warning('조례 조회 실패 %s %s: %s', s['sido'], s['sigungu'], e)
            rules, state = [], ordinances.UNVERIFIED
        s['rules'] = list(rules)
        s['ordinance_state'] = state
        label = f"{s['sido']} {s['sigungu']}"
        if state == ordinances.NO_RULE:
            no_rule.append(label)
        elif state != ordinances.HAS_RULES:
            unverified.append(label)

    meta['ordinance_auth_error'] = ordinances.auth_failure()
    # 조례상 이격 제한이 없다고 **확인된** 지자체. 보류가 아니라 정상 판정이다.
    meta['no_rule'] = no_rule
    # 확인하지 못한 지자체. 이 조각만 판정 보류로 센다.
    meta['unverified'] = unverified
    meta['pending_area_m2'] = sum(
        s['area_m2'] for s in slices
        if s['ordinance_state'] not in (ordinances.HAS_RULES, ordinances.NO_RULE))
    return slices, meta


def emd_at(lat: float, lng: float) -> dict | None:
    """
    한 좌표가 속한 **읍·면·동 경계** → {full_name, sido, sigungu, emd, rings}.

    사업지 주소를 검색해 지도를 옮길 때, 그 자리가 어느 면에 속하는지를 면으로
    보여 주기 위한 것이다. 주소만 찍으면 점 하나뿐이라 부지가 행정구역 어디에
    걸치는지 알 수 없고, 경계를 벗어난 곳을 사업지로 잡아도 눈치채지 못한다.

    좌표를 못 찾으면 None. 실패를 예외로 올리지 않는다 — 지도 보조 기능이라
    검색 자체를 죽일 이유가 없다.
    """
    try:
        pt = geo.point_metric(lat, lng)
    except Exception:                                           # noqa: BLE001
        return None
    minx, miny, maxx, maxy = pt.buffer(200).bounds
    params = {
        'SERVICE': 'WFS', 'REQUEST': 'GetFeature', 'VERSION': '1.1.0',
        'KEY': settings.VWORLD_API_KEY,
        'DOMAIN': getattr(settings, 'VWORLD_DOMAIN', '') or 'localhost',
        'TYPENAME': EMD_LAYER,
        'SRSNAME': geo.METRIC_CRS,
        'BBOX': ','.join(f'{v:.2f}' for v in (minx, miny, maxx, maxy)),
        'OUTPUT': 'application/json',
        'MAXFEATURES': '10',
    }

    def call() -> dict:
        res = LayerProvider.get(WFS_URL, params, timeout=60.0)
        res.raise_for_status()
        ct = (res.headers.get('content-type') or '').lower()
        if 'json' not in ct:
            raise BoundaryUnavailable(f'읍면동 경계 응답이 JSON이 아닙니다 ({ct})')
        return res.json()

    try:
        payload = httpcache.get_or_set('vworld_wfs', params, call)
    except Exception as e:                                      # noqa: BLE001
        logger.info('읍면동 경계 조회 실패 (%.5f, %.5f): %s', lat, lng, e)
        return None

    # bbox로 받으므로 이웃 면이 섞여 온다. **점을 실제로 품은** 것을 고른다.
    #
    # ⚠️ 이 응답은 SRSNAME으로 EPSG:5179를 지정해 받으므로 **이미 미터
    #    좌표**다. `to_metric`을 한 번 더 걸면 4326으로 알고 변환해 좌표가
    #    inf로 폭발한다(실측 — 링 760점이 전부 inf가 되어 JSON 직렬화에서
    #    500이 났다). `split()`도 같은 이유로 재투영하지 않는다.
    best = None
    for f in payload.get('features') or []:
        gm = geo.geom_from_geojson(f.get('geometry'))
        if gm is None:
            continue
        if gm.contains(pt):
            best = (f, gm)
            break
        if best is None:                    # 경계에 딱 걸치면 가장 가까운 것
            best = (f, gm)
    if best is None:
        return None

    props = best[0].get('properties') or {}
    full = (props.get('full_nm') or '').strip()
    # ⚠️ full_nm이 읍면동까지 붙은 3~4단계라 `split_full_name`을 그대로 쓰면
    #    **읍면동 이름이 시·군·구 자리에 들어간다**(실측: '강원특별자치도
    #    횡성군 둔내면' → 시군구 '둔내면'). 조례를 그 이름으로 찾으면 당연히
    #    없다. 마지막 토큰(읍면동)을 떼고 시군구 규칙을 적용한다.
    parts = full.split()
    sido, sigungu = split_full_name(' '.join(parts[:-1]) if len(parts) > 2 else full)
    return {
        'full_name': full,
        'sido': sido,
        'sigungu': sigungu,
        'emd': (props.get('emd_kor_nm') or '').strip(),
        'rings': geo.rings_4326(best[1], precision=5),
    }
