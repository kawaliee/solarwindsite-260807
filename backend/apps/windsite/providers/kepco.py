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
