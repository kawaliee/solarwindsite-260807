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


class LawApiAuthError(LawApiError):
    """
    사용자 검증 실패 — 조례·법령 부재와 반드시 구분해야 한다.

    law.go.kr은 OC와 별개로 **계정에 등록된 서버 IP/도메인**을 대조한다.
    등록 IP에서 온 요청이 아니면 OC가 맞아도 거절한다. 국내 가정용·모바일
    회선은 유동 IP라 이 상황이 주기적으로 재발한다.

    이때 응답이 <Response><result>…</result></Response> 형태여서 <law>가
    0건으로 파싱된다. 그대로 두면 '해당 지자체에 조례가 없다'로 읽혀,
    IP만 고치면 되는 문제가 데이터 부재로 둔갑한다.
    """


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
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise LawApiError(f'XML 파싱 실패: {text[:200]}') from e

    # 정상 응답은 <LawSearch>/<Law> 등이고 <result>를 갖지 않는다.
    # 오류만 <Response><result>메시지</result><msg>안내</msg></Response> 로 온다.
    if root.tag == 'Response':
        msg = (root.findtext('result') or '').strip()
        detail = (root.findtext('msg') or '').strip()
        full = f'{msg} {detail}'.strip() or '알 수 없는 오류'
        if '검증' in msg or '인증' in msg or 'IP' in detail:
            raise LawApiAuthError(full)
        raise LawApiError(full)

    return root


# ======================================================================
# 검색
# ======================================================================
#: 법령명에 쓰이는 가운뎃점 변종 — 원문은 'ㆍ'(U+318D), 사람이 적을 땐 '·'(U+00B7)를
#: 쓰는 일이 많아 그대로 비교하면 "법령을 찾지 못했습니다"가 난다.
_MIDDOT = str.maketrans({'·': 'ㆍ', '‧': 'ㆍ', '・': 'ㆍ', '•': 'ㆍ'})


def normalize_law_name(name: str) -> str:
    """가운뎃점 변종과 공백을 통일해 법령명을 비교 가능한 형태로 만든다."""
    return re.sub(r'\s+', '', (name or '').translate(_MIDDOT))


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
    # 완전일치(가운뎃점 정규화 후) → 현행 → 그 외 순
    key = normalize_law_name(name)
    out.sort(key=lambda r: (normalize_law_name(r['name']) != key, r['status'] != '현행'))
    return out


def search_admin_rule(name: str, display: int = 10) -> list[dict]:
    """
    행정규칙(훈령·예규·고시·지침) 검색.

    「육상풍력 개발사업 환경성평가 지침」처럼 법령이 아닌 행정규칙은
    target=law로는 절대 찾을 수 없다.
    """
    root = _get(SEARCH_URL, {'target': 'admrul', 'query': name, 'display': str(display)})
    out = []
    for el in root.findall('admrul'):
        out.append({
            'name': (el.findtext('행정규칙명') or '').strip(),
            'rule_id': el.findtext('행정규칙ID') or '',
            'mst': el.findtext('행정규칙일련번호') or '',
            'kind': el.findtext('행정규칙종류') or '',
            'effective_date': el.findtext('시행일자') or '',
            'ministry': el.findtext('소관부처명') or '',
            'detail_url': el.findtext('행정규칙상세링크') or '',
        })
    key = normalize_law_name(name)
    out.sort(key=lambda r: normalize_law_name(r['name']) != key)
    return out


def fetch_law_appendices(mst: str) -> list[dict]:
    """
    법령의 별표 목록과 본문.

    환경영향평가 대상 규모처럼 **판정에 직결되는 수치가 조문이 아니라 별표에**
    있는 경우가 많다. 조문만 대조하면 정작 중요한 기준을 검증하지 못한다.
    """
    root = _get(SERVICE_URL, {'target': 'law', 'MST': mst}, timeout=90.0)
    out = []
    for b in root.iter('별표단위'):
        raw_no = (b.findtext('별표번호') or '').strip()
        sub = (b.findtext('별표가지번호') or '').strip()
        out.append({
            'no': str(int(raw_no)) if raw_no.isdigit() else raw_no,
            'sub_no': str(int(sub)) if sub.isdigit() and int(sub) else '',
            'title': (b.findtext('별표제목') or '').strip(),
            'kind': (b.findtext('별표구분') or '').strip(),
            'text': re.sub(r'[ \t]{2,}', ' ', (b.findtext('별표내용') or '')).strip(),
        })
    return out


def parse_appendix_label(label: str) -> tuple[str, str]:
    """'시행령 별표3' / '[별표 4의2]' → ('3','') / ('4','2'). 아니면 ('','')"""
    m = re.search(r'별\s*표\s*(\d+)(?:\s*의\s*(\d+))?', label or '')
    if not m:
        return '', ''
    return m.group(1), (m.group(2) or '')


def find_appendix(appendices: list[dict], label: str) -> dict | None:
    no, sub = parse_appendix_label(label)
    if not no:
        return None
    for a in appendices:
        if a['no'] == no and (a['sub_no'] or '') == (sub or ''):
            return a
    return None


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
    return {'meta': meta, 'articles': articles, 'addenda': _addenda(root)}


#: 부칙에서 골라낼 문구. 경과조치·적용례가 있으면 조례 개정 전에 허가를 받은
#: 사업에 종전 기준이 적용될 수 있어, 이격거리 판정 결과가 통째로 뒤집힌다.
_ADDENDA_KEYS = ('경과조치', '적용례', '종전의', '시행일')


#: 부칙 회차 구분자. API가 모든 개정 부칙을 한 덩어리로 돌려주므로
#: 머리말을 기준으로 잘라야 회차별로 읽을 수 있다.
#: 날짜 표기가 두 가지다 — 옛 회차는 '(2003·06·05)', 최근 회차는 '<2025. 2. 28.>'.
#: 하나만 처리하면 정작 중요한 최근 개정의 공포일을 놓친다.
_ADDENDA_SPLIT = re.compile(
    r'부\s*칙\s*(?:[(（<]\s*(?:조례\s*제\s*\d+\s*호\s*,\s*)?'
    r'(\d{4})\s*[·.\-]\s*(\d{1,2})\s*[·.\-]\s*(\d{1,2})\s*\.?\s*[)）>])?')


def _addenda(root) -> list[dict]:
    """
    부칙 → [{'promulgated': 'YYYYMMDD'|'', 'text': …}] (최신이 뒤).

    조문(articles)에는 부칙이 **들어 있지 않다.** 별도 태그로 오고, 게다가
    모든 개정 회차가 한 문자열로 붙어 있어 직접 잘라야 한다.
    """
    blob = ''
    for bu in root.iter('부칙내용'):
        blob += ' ' + ' '.join((bu.text or '').split())
    if not blob.strip():
        for bu in root.iter('부칙'):
            blob += ' ' + ' '.join(''.join(bu.itertext()).split())
    blob = blob.strip()
    if not blob:
        return []

    out: list[dict] = []
    marks = list(_ADDENDA_SPLIT.finditer(blob))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(blob)
        text = blob[m.end():end].strip()
        if not text:
            continue
        date = ''
        if m.group(1):
            date = '%s%02d%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))
        out.append({'promulgated': date, 'text': text})
    return out or [{'promulgated': '', 'text': blob}]


def relevant_addenda(addenda: list[dict], limit: int = 6) -> str:
    """
    부칙 중 경과조치·적용례만 추려 인용 가능한 문자열로 만든다.

    최신 개정이 대개 결정적이므로 **뒤에서부터** 고른다. 판정은 하지 않는다 —
    부칙이 특정 사업에 적용되는지는 관할 지자체가 판단할 문제이고, 여기서는
    사용자가 직접 읽을 수 있게 근거를 붙여주는 것까지가 역할이다.
    """
    def fmt(a: dict) -> str:
        d = a.get('promulgated') or ''
        return (f'[{d} 개정] ' if d else '') + (a.get('text') or '')[:900]

    cand = [a for a in (addenda or [])
            if any(k in (a.get('text') or '') for k in _ADDENDA_KEYS[:2])]
    # 발전사업허가·풍력을 직접 언급한 경과조치를 맨 앞에 둔다. 이 조항 하나가
    # 이격거리 판정을 통째로 뒤집으므로 다른 경과조치에 묻히면 안 된다.
    hot = [a for a in cand if any(k in (a.get('text') or '') for k in _POWER_KEYS)]
    rest = [a for a in reversed(cand) if a not in hot]
    return '\n\n'.join(fmt(a) for a in (hot + rest)[:limit])


#: 발전사업 경과조치를 가려내는 말
_POWER_KEYS = ('발전사업허가', '전기사업법', '풍력', '태양에너지', '재생에너지')

#: 부칙 제1조의 시행일 문언
_EFF_IMMEDIATE = re.compile(r'공포한?\s*날부터\s*시행')
_EFF_AFTER_DAYS = re.compile(r'공포\s*후\s*(\d+)\s*일이?\s*지난\s*날부터\s*시행')
_EFF_EXPLICIT = re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일부터\s*시행')


def power_transition(addenda: list[dict]) -> dict | None:
    """
    발전사업 경과조치를 담은 부칙 회차를 찾아 그 **시행일**과 원문을 돌려준다.

    소급 여부를 가르는 날짜는 조례 전체의 최신 시행일이 아니라, 그 경과조치를
    담은 **개정 조례의 시행일**이다. 부칙의 "이 조례 시행 전에"에서 '이 조례'는
    조례 전문이 아니라 그 개정 조례를 가리키기 때문이다.

    삼척시가 그 예다. 조례 최신 시행일은 2025-08-08이지만 풍력 경과조치는
    **2025-02-28 개정 부칙**에 있다. 최신 시행일로 비교하면 그 사이에 허가를
    받은 사업이 잘못 판정된다.

    반환 {'date','promulgated','text','date_basis'}
    date_basis는 시행일을 어떻게 정했는지다. 'PROMULGATED'는 시행일 문언을
    읽지 못해 공포일로 대신한 경우이므로 화면에서 단정하면 안 된다.
    """
    from datetime import date as _date, timedelta

    for a in reversed(addenda or []):
        text = a.get('text') or ''
        if '경과조치' not in text or not any(k in text for k in _POWER_KEYS):
            continue
        pub = (a.get('promulgated') or '').strip()

        m = _EFF_EXPLICIT.search(text)
        if m:
            return {'date': '%s%02d%02d' % (m.group(1), int(m.group(2)), int(m.group(3))),
                    'promulgated': pub, 'text': text, 'date_basis': 'EXPLICIT'}
        if len(pub) == 8 and pub.isdigit():
            base = _date(int(pub[:4]), int(pub[4:6]), int(pub[6:]))
            m = _EFF_AFTER_DAYS.search(text)
            if m:
                return {'date': (base + timedelta(days=int(m.group(1)))).strftime('%Y%m%d'),
                        'promulgated': pub, 'text': text, 'date_basis': 'AFTER_DAYS'}
            basis = 'IMMEDIATE' if _EFF_IMMEDIATE.search(text) else 'PROMULGATED'
            return {'date': pub, 'promulgated': pub, 'text': text, 'date_basis': basis}
        return {'date': '', 'promulgated': pub, 'text': text,
                'date_basis': 'PROMULGATED'}
    return None


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


# ======================================================================
# 별표(첨부 HWP)
# ======================================================================
def fetch_ordinance_appendices(mst: str) -> list[dict]:
    """
    자치법규의 별표 목록.

    ⚠️ 실측 확인 — 별표 본문(`별표내용`)은 비어 있고 **HWP 첨부로만** 제공된다.
       청도군처럼 이격거리 기준이 조문이 아니라 별표에 있는 지자체가 있어,
       별표를 보지 않으면 "규정 없음"으로 잘못 판정하게 된다.
    """
    root = _get(SERVICE_URL, {'target': 'ordin', 'MST': mst}, timeout=90.0)
    out = []
    for b in root.iter('별표단위'):
        out.append({
            'no': (b.findtext('별표번호') or '').strip(),
            'sub_no': (b.findtext('별표가지번호') or '').strip(),
            'title': (b.findtext('별표제목') or '').strip(),
            'kind': (b.findtext('별표구분') or '').strip(),
            'file_type': (b.findtext('별표첨부파일구분') or '').strip(),
            'file_url': (b.findtext('별표첨부파일명') or '').strip(),
            'text': (b.findtext('별표내용') or '').strip(),
        })
    return out


def fetch_appendix_text(file_url: str, timeout: float = 90.0) -> str:
    """
    별표 HWP를 내려받아 본문 텍스트를 뽑는다.

    hwp-hwpx-parser는 이 문서(표 위주 별표)에서 본문을 거의 못 뽑아내,
    HWP5 BodyText 스트림의 문단 텍스트 레코드를 직접 읽는다.
    """
    if not file_url:
        return ''
    # 응답 헤더의 파일명은 URL 쿼리에 붙어 오기도 한다 — flSeq만 남겨 요청한다
    url = re.sub(r'&flNm=.*$', '', file_url)
    res = httpx.get(url, timeout=timeout, follow_redirects=True,
                    headers={'User-Agent': 'windsite-lawcheck/1.0'})
    res.raise_for_status()
    return extract_hwp_text(res.content)


def extract_hwp_text(blob: bytes) -> str:
    """HWP5(OLE) 바이트에서 문단 텍스트만 추출한다."""
    import struct
    import zlib

    try:
        import olefile
    except ImportError:                                         # pragma: no cover
        logger.warning('olefile 미설치 — 별표 HWP를 읽을 수 없습니다.')
        return ''

    import io
    try:
        ole = olefile.OleFileIO(io.BytesIO(blob))
    except Exception:                                           # noqa: BLE001
        logger.warning('HWP OLE 판독 실패')
        return ''

    HWPTAG_PARA_TEXT = 67
    chunks: list[str] = []
    for entry in ole.listdir():
        if not entry or entry[0] != 'BodyText':
            continue
        data = ole.openstream(entry).read()
        try:
            data = zlib.decompress(data, -15)                   # 배포용 압축 스트림
        except zlib.error:
            pass                                                # 비압축 문서
        i = 0
        while i < len(data) - 4:
            header = struct.unpack('<I', data[i:i + 4])[0]
            tag = header & 0x3FF
            size = (header >> 20) & 0xFFF
            i += 4
            if tag == HWPTAG_PARA_TEXT:
                chunks.append(data[i:i + size].decode('utf-16le', 'ignore'))
            i += size
    text = ''.join(chunks)
    # HWP 제어문자(표/그림 앵커 등)를 공백으로 치환
    return re.sub(r'[\x00-\x1f]', ' ', text)


def law_detail_url(law_id: str) -> str:
    if not law_id:
        return 'https://www.law.go.kr'
    return f'{DETAIL_BASE}?lsId={urllib.parse.quote(law_id)}'
