"""
국가법령정보 공동활용 OPEN API 클라이언트
---------------------------------------------------------------
법제처가 제공하는 DRF 엔드포인트로 **법령·자치법규 원문**을 가져온다.
지금까지 시드 데이터의 상당수가 2차 자료(언론보도 등) 기반이라
`confidence='LOW'`였는데, 이 모듈로 1차 원문 대조를 자동화한다.

  검색: https://www.law.go.kr/DRF/lawSearch.do?OC=..&target=law|ordin&type=XML&query=..
  본문: https://www.law.go.kr/DRF/lawService.do?OC=..&target=law|ordin&type=XML&MST=..

인증(OC)
  신청 이메일의 ID(@ 앞부분)가 그대로 인증값이다. 미설정 시 공용 데모 계정
  'test'로 동작하지만 **사용량 제한이 있어 운영에는 자체 발급 값을 쓴다.**
  발급: https://open.law.go.kr → 오픈API 신청

⚠️ 응답 XML의 태그 구조가 target에 따라 다르다 (실측 확인).
     target=law   → <조문단위><조문번호><조문내용><항>…
     target=ordin → <조><조문번호><조제목><조내용>  (항이 조내용에 통째로 들어있음)
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

SEARCH_URL = 'https://www.law.go.kr/DRF/lawSearch.do'
SERVICE_URL = 'https://www.law.go.kr/DRF/lawService.do'
DETAIL_BASE = 'https://www.law.go.kr/LSW/lsInfoP.do'


class LawApiError(RuntimeError):
    pass


def _oc() -> str:
    return getattr(settings, 'LAW_API_OC', '') or 'test'


def is_demo_account() -> bool:
    """공용 데모 계정으로 동작 중인지 — 결과 신뢰도 표기에 쓴다."""
    return _oc() == 'test'


def _get(url: str, params: dict, timeout: float = 60.0) -> ET.Element:
    params = {'OC': _oc(), 'type': 'XML', **params}
    res = httpx.get(url, params=params, timeout=timeout,
                    headers={'User-Agent': 'windsite-lawcheck/1.0'})
    res.raise_for_status()
    text = res.text
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise LawApiError(f'XML 파싱 실패: {text[:200]}') from e


# ======================================================================
# 검색
# ======================================================================
def search_law(name: str, display: int = 20) -> list[dict]:
    """법령명으로 검색. 정확히 일치하는 현행 법령을 앞에 둔다."""
    root = _get(SEARCH_URL, {'target': 'law', 'query': name, 'display': str(display)})
    out = []
    for el in root.findall('law'):
        out.append({
            'name': (el.findtext('법령명한글') or '').strip(),
            'law_id': el.findtext('법령ID') or '',
            'mst': el.findtext('법령일련번호') or '',
            'effective_date': el.findtext('시행일자') or '',
            'promulgated': el.findtext('공포일자') or '',
            'status': el.findtext('현행연혁코드') or '',
            'ministry': el.findtext('소관부처명') or '',
            'detail_url': el.findtext('법령상세링크') or '',
        })
    # 완전일치 → 현행 → 그 외 순
    out.sort(key=lambda r: (r['name'] != name, r['status'] != '현행'))
    return out


def search_ordinance(sigungu: str, keyword: str = '', display: int = 20) -> list[dict]:
    """
    자치법규(조례) 검색.

    조례명은 지자체마다 다르다(도시계획 / 군계획 / 도시·군계획 …).
    제명이 바뀐 사례도 있어 지자체명만으로 넓게 찾은 뒤 이름으로 거른다.
    """
    query = f'{sigungu} {keyword}'.strip()
    root = _get(SEARCH_URL, {'target': 'ordin', 'query': query, 'display': str(display)})
    out = []
    for el in root.findall('law'):
        out.append({
            'name': (el.findtext('자치법규명') or '').strip(),
            'org': (el.findtext('지자체기관명') or '').strip(),
            'ordin_id': el.findtext('자치법규ID') or '',
            'mst': el.findtext('자치법규일련번호') or '',
            'effective_date': el.findtext('시행일자') or '',
            'promulgated': el.findtext('공포일자') or '',
            'detail_url': el.findtext('자치법규상세링크') or '',
        })
    return out


# ======================================================================
# 본문
# ======================================================================
def fetch_law_articles(mst: str) -> dict:
    """법령 전문 → {'meta': {...}, 'articles': [{no, title, text}]}"""
    root = _get(SERVICE_URL, {'target': 'law', 'MST': mst}, timeout=90.0)
    meta = {
        'name': root.findtext('.//법령명_한글') or '',
        'law_id': root.findtext('.//법령ID') or '',
        'effective_date': root.findtext('.//시행일자') or '',
        'promulgated': root.findtext('.//공포일자') or '',
        'ministry': root.findtext('.//소관부처') or '',
        'revision': root.findtext('.//제개정구분') or '',
    }

    articles: list[dict] = []
    for j in root.iter('조문단위'):
        head = (j.findtext('조문내용') or '').strip()
        title = (j.findtext('조문제목') or '').strip()
        if not title and not head.startswith('제'):
            continue                       # 편/장/절 제목 블록은 건너뛴다
        parts = [head]
        for h in j.iter('항'):
            parts.append((h.findtext('항내용') or '').strip())
            for ho in h.iter('호'):
                parts.append((ho.findtext('호내용') or '').strip())
        articles.append({
            'no': (j.findtext('조문번호') or '').strip(),
            'sub_no': (j.findtext('조문가지번호') or '').strip(),
            'title': title,
            'text': '\n'.join(p for p in parts if p),
        })
    return {'meta': meta, 'articles': articles}


def fetch_ordinance_articles(mst: str) -> dict:
    """자치법규 전문 → {'meta': {...}, 'articles': [{no, title, text}]}"""
    root = _get(SERVICE_URL, {'target': 'ordin', 'MST': mst}, timeout=90.0)
    meta = {
        'name': root.findtext('.//자치법규명') or '',
        'ordin_id': root.findtext('.//자치법규ID') or '',
        'org': root.findtext('.//지자체기관명') or '',
        'effective_date': root.findtext('.//시행일자') or '',
        'promulgated': root.findtext('.//공포일자') or '',
        'department': root.findtext('.//담당부서명') or '',
    }
    articles = [{
        'no': (jo.findtext('조문번호') or '').strip(),
        'sub_no': '',
        'title': (jo.findtext('조제목') or '').strip(),
        'text': (jo.findtext('조내용') or '').strip(),
    } for jo in root.iter('조')]
    return {'meta': meta, 'articles': articles}


# ======================================================================
# 조문 번호 표기 변환
# ======================================================================
def parse_article_label(label: str) -> tuple[str, str]:
    """
    '제61조', '제15조의2', '제20조의2(발전시설 허가의 기준)' → ('61', '') / ('15', '2')
    조문을 특정하지 못하면 ('', '')를 돌려주고, 호출부는 대조를 건너뛴다.
    """
    m = re.search(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', label or '')
    if not m:
        return '', ''
    return m.group(1), (m.group(2) or '')


def ordinance_article_key(no: str) -> tuple[str, str]:
    """
    자치법규 조문번호는 '002002'처럼 6자리(조 4 + 가지 2)로 온다 (실측 확인).
    '002002' → ('20', '2'),  '000700' → ('7', '')
    """
    if not no:
        return '', ''
    if no.isdigit() and len(no) == 6:
        return str(int(no[:4])), (str(int(no[4:])) if int(no[4:]) else '')
    return str(int(no)) if no.isdigit() else no, ''


def find_article(articles: list[dict], label: str, *, ordinance: bool = False) -> dict | None:
    """조문 목록에서 '제61조' / '제20조의2' 형태의 표기에 해당하는 조문을 찾는다."""
    want_no, want_sub = parse_article_label(label)
    if not want_no:
        return None
    for a in articles:
        if ordinance:
            no, sub = ordinance_article_key(a['no'])
        else:
            no, sub = a['no'].lstrip('0') or a['no'], a.get('sub_no', '')
        if no == want_no and (sub or '') == (want_sub or ''):
            return a
    return None


def law_detail_url(law_id: str) -> str:
    if not law_id:
        return 'https://www.law.go.kr'
    return f'{DETAIL_BASE}?lsId={urllib.parse.quote(law_id)}'
