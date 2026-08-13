"""
한전 분산전원 연계정보 — 계통 여유용량
---------------------------------------------------------------
전력데이터 개방 포털(bigdata.kepco.co.kr)의 분산전원 연계정보 API.
그동안 "한전이 공개하지 않는다"며 UNKNOWN으로 두었던 **접속 가능 용량**을 채운다.

  https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do
    apiKey(필수, 40자리) · metroCd(시도) · cityCd(시군구) · addrLidong(읍면동)
    · addrLi(리) · addrJibun(번지) · substCd(변전소코드) · returnType(json|xml)

응답 (가이드 문서 확인)
  substNm  변전소명        jsSubstPwr 변전소 용량      substPwr 변전소 누적연계용량
  mtrNo    변압기 번호      jsMtrPwr   변압기 용량      mtrPwr   변압기 누적연계용량
  dlNm     배전선로(DL)명   jsDlPwr    DL 용량          dlPwr    DL 누적연계용량
  vol1     변전소 여유용량   vol2       변압기 여유용량   vol3     DL 여유용량

⚠️ 실측 확인 사항
  · 호출 간격 제한이 있다. 연속 호출하면 404/401이 나므로 캐시를 반드시 경유한다
  · jsSubstPwr·jsMtrPwr·jsDlPwr(각 설비 용량)는 전국 8,397건 **전부 0**이다.
    한전 쪽 미제공 값으로 보이므로 판정에 쓰지 않는다
  · 단위가 문서에 명시되지 않았다. 값의 크기(변전소 누적연계 9~25만)로 보아 kW로
    해석하되, 결과 문구에 이 전제를 밝힌다
"""
from __future__ import annotations

import logging
import re

import httpx
from django.conf import settings

from .. import httpcache

logger = logging.getLogger(__name__)

DEFAULT_URL = 'https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do'

#: 변전소명 대조용 접미사 — OSM은 '진보변전소', 한전은 '진보SA'로 표기한다
_SUFFIXES = ('변전소', '변전소S/S', 'S/S', 'SS', 'SA', 'CB', '개폐소')


class KepcoGridError(RuntimeError):
    pass


def normalize_substation(name: str) -> str:
    """'진보변전소' / '진보SA' → '진보' (양쪽 표기를 맞춘다)"""
    s = re.sub(r'\s+', '', name or '')
    s = re.sub(r'\(.*?\)', '', s)
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s


class KepcoGridClient:
    """분산전원 연계정보 조회"""

    @staticmethod
    def url() -> str:
        return getattr(settings, 'KEPCO_GRID_URL', '') or DEFAULT_URL

    @classmethod
    def fetch(cls, metro_cd: str = '', city_cd: str = '') -> list[dict]:
        """
        시도(·시군구) 단위 조회. 지역을 좁힐수록 응답이 가볍다.
        호출 간격 제한이 있어 반드시 캐시를 경유한다.
        """
        key = settings.KEPCO_API_KEY
        if not key:
            raise KepcoGridError('KEPCO_API_KEY가 설정되지 않았습니다.')

        params: dict[str, str] = {'apiKey': key, 'returnType': 'json'}
        if metro_cd:
            params['metroCd'] = metro_cd
        if city_cd:
            params['cityCd'] = city_cd

        def call() -> list[dict]:
            res = httpx.get(cls.url(), params=params, timeout=90.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            if res.status_code in (401, 404):
                # 호출 간격 제한에서도 이 코드가 나온다 — 데이터 부재와 구분해 알린다
                raise KepcoGridError(
                    f'조회 거절 (HTTP {res.status_code}). 호출 간격 제한일 수 있습니다.')
            res.raise_for_status()
            payload = res.json()
            return payload.get('data') or []

        return httpcache.get_or_set('kepco_grid', params, call)

    # ------------------------------------------------------------------
    @staticmethod
    def summarize(rows: list[dict]) -> dict[str, dict]:
        """변전소별로 집계 — 여유용량과 선로 목록."""
        out: dict[str, dict] = {}
        for r in rows:
            name = (r.get('substNm') or '').strip()
            if not name:
                continue
            rec = out.setdefault(name, {
                'name': name,
                'code': r.get('substCd', ''),
                'substation_margin_kw': 0.0,   # vol1 — 변전소 여유용량
                'transformer_margin_kw': 0.0,  # vol2 — 변압기 여유용량
                'best_line_margin_kw': 0.0,    # vol3 최대 — DL 여유용량
                'best_line': '',
                'lines': [],
            })
            v1 = _num(r.get('vol1'))
            v2 = _num(r.get('vol2'))
            v3 = _num(r.get('vol3'))
            rec['substation_margin_kw'] = max(rec['substation_margin_kw'], v1)
            rec['transformer_margin_kw'] = max(rec['transformer_margin_kw'], v2)
            if v3 > rec['best_line_margin_kw']:
                rec['best_line_margin_kw'] = v3
                rec['best_line'] = (r.get('dlNm') or '').strip()
            rec['lines'].append({
                'code': r.get('dlCd', ''),
                'name': (r.get('dlNm') or '').strip(),
                'connected_kw': _num(r.get('dlPwr')),
                'margin_kw': v3,
            })
        for rec in out.values():
            rec['lines'].sort(key=lambda l: -l['margin_kw'])
            rec['lines'] = rec['lines'][:20]
        return out


def _num(v) -> float:
    try:
        return float(str(v).replace(',', '').strip() or 0)
    except (TypeError, ValueError):
        return 0.0


# ======================================================================
# 지역별 공급가능 변전소 (공공데이터포털 15128065)
# ----------------------------------------------------------------------
# 읍면동마다 어느 변전소가 공급하는지를 알려준다. OSM 거리와는 다른 정보다 —
# 가까워도 공급 대상이 아닐 수 있고, 멀어도 공급 대상일 수 있다.
#
# **변전소명은 첫 글자만 남기고 가려져 있다**(삼*, 도*, 태* — 전국 209개).
# 국가기밀시설이라 위치와 이름을 공개하지 않는 정책이다. 그래서 이 자료만으로
# 변전소를 특정할 수 없고, 좌표는 어디서도 얻을 수 없다.
#
# 그럼에도 싣는 이유는 둘이다.
#   · Overpass가 막혀 OSM 변전소를 못 받아도 "이 읍면동에 공급 가능한
#     변전소가 N개소 있다"는 사실은 남길 수 있다
#   · 첫 글자로 여유용량 자료와 대조해 후보를 좁힐 수 있다
# ======================================================================
#: 시·도 → 여유용량 API의 metroCd. 후보를 전국에서 고르면 '삼*'에
#: 삼계·삼미·삼죽·삼척이 모두 걸려 좁혀지지 않는다. 도 단위로 줄이면
#: 대개 하나로 특정된다(강원 '삼*' → 삼척).
#: 값은 법정동 시·도 코드다. 강원은 특별자치도 전환으로 42가 아니라 51이며,
#: 42로 부르면 404가 온다(실측).
METRO_CD = {
    '서울특별시': '11', '부산광역시': '26', '대구광역시': '27', '인천광역시': '28',
    '광주광역시': '29', '대전광역시': '30', '울산광역시': '31', '세종특별자치시': '36',
    '경기도': '41', '강원특별자치도': '51', '강원도': '51',
    '충청북도': '43', '충청남도': '44',
    '전북특별자치도': '52', '전라북도': '52', '전라남도': '46',
    '경상북도': '47', '경상남도': '48', '제주특별자치도': '50',
}

SUPPLY_URL = ('https://api.odcloud.kr/api/15128065/v1/'
              'uddi:3a841aea-8d81-499a-a82a-ac6588c35b88')
SUPPLY_PAGE = 1000
SUPPLY_MAX_PAGES = 8


def fetch_supply_map() -> list[dict]:
    """전국 공급가능 변전소 표(4,557행). 한 번 받아 캐시한다."""
    key = getattr(settings, 'DATA_GO_KR_KEY', '')
    if not key:
        return []

    def call() -> list[dict]:
        out: list[dict] = []
        for page in range(1, SUPPLY_MAX_PAGES + 1):
            res = httpx.get(SUPPLY_URL,
                            params={'serviceKey': key, 'page': str(page),
                                    'perPage': str(SUPPLY_PAGE)},
                            timeout=90.0,
                            headers={'User-Agent': 'windsite-feasibility/1.0'})
            res.raise_for_status()
            d = res.json()
            rows = d.get('data') or []
            if not rows:
                break
            out.extend(rows)
            if len(out) >= int(d.get('totalCount') or 0):
                break
        return out

    try:
        return httpcache.get_or_set('kepco_supply', {'v': 1}, call)
    except Exception as e:                                      # noqa: BLE001
        logger.warning('공급가능 변전소 조회 실패: %s', e)
        return []


def supply_for(sido: str, sigungu: str, eupmyeondong: str = '') -> dict:
    """
    행정구역에 공급 가능한 변전소 목록.

    반환 {'names': ['삼*', …], 'scope': '근덕면'|'삼척시', 'masked': True}
    읍면동이 맞지 않으면 시군구 단위로 넓혀 답한다 — 없다고 하는 것보다 낫다.
    """
    rows = fetch_supply_map()
    if not rows:
        return {}

    def pick(pred) -> list[str]:
        return sorted({r['공급변전소'] for r in rows
                       if r.get('공급변전소') and pred(r)})

    if eupmyeondong:
        names = pick(lambda r: r.get('시군구') == sigungu
                     and r.get('읍면동') == eupmyeondong)
        if names:
            return {'names': names, 'scope': eupmyeondong, 'masked': True}
    names = pick(lambda r: r.get('시군구') == sigungu
                 and (not sido or r.get('시도') == sido))
    if names:
        return {'names': names, 'scope': sigungu, 'masked': True}
    return {}


def match_masked(masked: str, candidates: list[str]) -> list[str]:
    """
    '삼*' 처럼 가려진 이름에 맞는 후보를 고른다.

    첫 글자만 남아 있어 여럿이 걸릴 수 있다. 하나로 좁혀지지 않으면
    좁히지 않고 그대로 돌려준다 — 임의로 하나를 고르면 여유용량을 엉뚱한
    변전소 것으로 붙이게 된다.
    """
    head = (masked or '').replace('*', '').strip()
    if not head:
        return []
    return [c for c in candidates if c.startswith(head)]
