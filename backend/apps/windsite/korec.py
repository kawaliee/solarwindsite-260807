"""
전기위원회 자료 수집·판독 (korec.go.kr)
---------------------------------------------------------------
발전사업허가 **사례 대조**의 원본이다. 규제 판정에 "이 지역에서 이 정도
사업이 실제로 되고 있는가"라는 사실을 더한다.

⚠️ **점수·판정에 반영하지 않는다.** 사례는 참고 사실이지 허가 가능성의
근거가 아니다(생존 편향 — data/korec/README.md).

■ 세 갈래 자료

    ① 3MW 초과 발전사업 허가대장   3,230행 (2001~) · 위치·용량·허가일
    ② 위원회 개최결과              141회차 2,880안건 — **부결·보류·사유까지**
    ③ 위원회 회의록                165회차 — 사업주체·지번·용량·발언요지

①이 몸통이다. **3MW 초과만 싣기 때문에** 당사 사업 구간(3~100MW)과 정확히
겹친다. 앞서 검토한 「전국태양광 표준데이터」는 소규모 등록부라 3MW 이상이
전국 28건뿐이었고 홍성·장흥은 0건이었다. 같은 지역을 ①에서 세면 태양광
홍성 4건(최대 53.24MW)·장흥 1건(99MW)이 나온다.

②③은 ①이 말하지 않는 것을 말한다. ①에는 **허가된 것만** 남지만 ②에는
보류·부결과 그 사유가 실린다. ③에는 지번과 조건부 사유가 있다.

■ ⚠️ 서버가 프로그램 요청을 가려낸다

첨부는 `/commonfile/fileDownLoad.do`에 POST해서 받는데, 평범한 요청에는
**파일 대신 사이트 배너 JSON**이 온다(200 OK, application/json). 브라우저는
정상적으로 받는다.

차이는 헤더였다. 실제 내려받기는 폼 전송(target=_blank)이라 **내비게이션
요청**인데 프로그램 요청에는 `Sec-Fetch-Mode: navigate`가 없다. 그 헤더를
붙이자 곧바로 `application/pdf`가 왔다. NAV_HEADERS를 지우지 말 것.

■ 형식과 편집 얼개가 뒤죽박죽이다

    개최결과  HWP 118 · PDF 23      편집 얼개가 열 해 사이 세 번 바뀌었다
    회의록    HWP 142 · PDF 23      PDF만 2-up + 본문이 이미지 → OCR
    허가대장  PDF · 표              find_tables()

**형식은 파일 첫 바이트로 가른다.** 게시판이 어느 쪽이든 같은 이름으로 올려
두기 때문이다. HWP를 PDF로 열려 하면 조용히 빈 쪽이 되어 그 회차가 통째로
사라진다.
"""
from __future__ import annotations

import io
import logging
import re
import warnings

import httpx

logger = logging.getLogger(__name__)

BASE = 'https://www.korec.go.kr'
#: 위원회 개최결과 게시판
RESULT_BOARD = '/notice/result/selectNoticeList.do'
#: 공지사항 게시판 — 회의록·허가대장·허가취소 공고가 여기 있다
NOTICE_BOARD = '/notice/selectNoticeList.do'
DOWNLOAD_URL = BASE + '/commonfile/fileDownLoad.do'

#: 이 헤더가 없으면 서버가 파일 대신 배너 JSON을 돌려준다(실측).
#: 실제 내려받기가 폼 전송(내비게이션)이라 그 모양을 맞춘 것이다.
NAV_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
    'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
               'image/avif,image/webp,*/*;q=0.8'),
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'Referer': BASE + RESULT_BOARD,
}

_ATTACH = re.compile(r"cf_downloadFile\('([^']*)',\s*'([^']+)'\)")
#: 회차 — 파일명·본문 어디서든 뽑는다
ROUND_RE = re.compile(r'제\s*(\d{2,4})\s*차')
#: 게시 시각이 파일명 꼬리에 붙는다 — `…_w20260819155811.pdf`
_STAMP = re.compile(r'_w(\d{8})\d*\.\w+$')


# ══════════════════════════════════════════════════════════════════════
# 내려받기
# ══════════════════════════════════════════════════════════════════════
def fetch_files(board: str, page: int = 1,
                client: httpx.Client | None = None) -> list[dict]:
    """게시판 한 쪽의 첨부 목록. → [{'file_path','file_name','round','posted'}]"""
    close = client is None
    c = client or httpx.Client(timeout=60.0, follow_redirects=True,
                               headers=NAV_HEADERS)
    try:
        r = c.get(BASE + board, params={'pageIndex': page})
        r.raise_for_status()
        html = r.text
    finally:
        if close:
            c.close()

    out: list[dict] = []
    for m in _ATTACH.finditer(html):
        name = m.group(2)
        rd = ROUND_RE.search(name)
        st = _STAMP.search(name)
        out.append({
            'file_path': m.group(1) or '/notice',
            'file_name': name,
            'round': int(rd.group(1)) if rd else None,
            'posted': (f'{st.group(1)[:4]}-{st.group(1)[4:6]}-{st.group(1)[6:8]}'
                       if st else ''),
        })
    return out


def crawl_files(board: str, max_pages: int = 40) -> list[dict]:
    """
    게시판 전체 첨부 목록.

    쪽 번호를 넘겨도 서버가 마지막 쪽을 되풀이해 주므로, **같은 목록이
    두 번 나오면 끝난 것**으로 본다. 상단 고정 공지가 모든 쪽에 붙어 나오기
    때문에 파일명으로 중복을 걷어낸다.
    """
    seen: dict[str, dict] = {}
    prev: list[str] | None = None
    with httpx.Client(timeout=60.0, follow_redirects=True,
                      headers=NAV_HEADERS) as c:
        for pg in range(1, max_pages + 1):
            rows = fetch_files(board, pg, client=c)
            names = [r['file_name'] for r in rows]
            if not names or names == prev:
                break
            prev = names
            for r in rows:
                seen.setdefault(r['file_name'], r)
    return list(seen.values())


def download(file_path: str, file_name: str) -> bytes:
    """
    첨부 원본. 실패하면 예외를 올린다(호출부가 건별로 다룬다).

    ⚠️ NAV_HEADERS 없이 부르면 200 OK와 함께 **파일이 아닌 JSON**이 온다.
       내용을 확인하지 않으면 빈 판독으로 조용히 넘어간다.
    """
    with httpx.Client(timeout=120.0, follow_redirects=True,
                      headers=NAV_HEADERS) as c:
        c.get(BASE + RESULT_BOARD)               # 세션 쿠키
        r = c.post(DOWNLOAD_URL, data={'attachingFilePath': file_path,
                                       'attachingFileNm': file_name})
        r.raise_for_status()
    if r.content[:4] not in (b'%PDF', b'PK\x03\x04', b'\xd0\xcf\x11\xe0'):
        raise ValueError(
            f'파일이 아닌 응답을 받았습니다 ({r.headers.get("content-type")}, '
            f'{len(r.content):,}B). 서버가 프로그램 요청을 가려냈을 수 있습니다.')
    return r.content


def suffix_of(blob: bytes) -> str:
    """저장할 때 쓸 확장자. 내용과 이름이 어긋나면 나중에 열 수 없다."""
    return {b'%PDF': '.pdf', b'PK\x03\x04': '.hwpx'}.get(blob[:4], '.hwp')


# ══════════════════════════════════════════════════════════════════════
# 판독 — 형식별
# ══════════════════════════════════════════════════════════════════════
def read_document(blob: bytes) -> str:
    """
    첨부 → 본문. **형식은 파일 첫 바이트로 가른다.**

    확장자만 믿으면 안 되는 이유는 게시판이 PDF·HWP·HWPX를 같은 이름으로
    섞어 올려 두기 때문이다(개최결과 141건 중 HWP 118 · PDF 23).
    """
    from apps.windsite import lawapi

    head = blob[:4]
    if head == b'%PDF':
        return extract_text(blob)
    if head == b'PK\x03\x04':
        return _denoise(lawapi.extract_hwpx_text(blob))
    return _denoise(lawapi.extract_hwp_text(blob))


#: HWP 본문에서 나오는 찌꺼기. 문단·표 구분자가 글자로 잘못 풀려 나온 것들이다
#: (`捤獥` `氠瑢` `汫`). 안건명 꼬리에 붙으면 이름이 어긋나 회차 매칭이 깨지므로
#: 걷어낸다. **한글·영문·숫자·문장부호는 건드리지 않는다.**
_HWP_NOISE = re.compile(
    '['
    '⺀-⿿'          # 한자 부수·확장
    '㐀-䶿'          # 한자 확장 A
    '一-鿿'          # 한자 기본 — 이 문서들에는 쓰이지 않는다
    'ऀ-ॿ'          # 데바나가리
    '-'          # 사용자 영역 — 문단 기호가 여기로 풀린다
    ']+')


def _denoise(text: str) -> str:
    return re.sub(r'[ \t]{3,}', '  ', _HWP_NOISE.sub(' ', text))


def extract_text(blob: bytes) -> str:
    """
    PDF → 본문. **좌표 순서로 정렬**한다.

    정렬하지 않으면 표·괄호 때문에 글자가 뒤섞여 안건명이 무너진다
    (실측: '구역전기사업자산업단지 개사' 처럼 조각남).
    """
    import fitz

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with fitz.open(stream=blob, filetype='pdf') as d:
            return '\n'.join(p.get_text('text', sort=True) for p in d)


#: 2-up 판정 기준. A4 세로(595pt)보다 넓으면 두 쪽을 한 장에 앉힌 것으로 본다.
_2UP_MIN_WIDTH = 700.0
#: 이 미만이면 텍스트 층이 비었다고 보고 OCR로 넘긴다(반쪽 기준).
_OCR_KO_FLOOR = 40


def read_pages(blob: bytes, ocr: bool = True, dpi: int = 300,
               force_ocr: bool = False) -> list[str]:
    """
    PDF → 논리 쪽 목록.

    ■ 2-up 분할
      842pt 가로 편집은 A4 두 쪽을 한 장에 앉힌 것이다. 가운데를 기준으로
      좌·우를 갈라 각각 한 쪽으로 다룬다. 가르지 않으면 좌우 단의 문장이
      한 줄씩 번갈아 나와 읽을 수 없다.

    ■ 낱말 좌표로 줄을 다시 세운다
      HWP에서 뽑은 PDF는 글자가 그려진 순서가 읽는 순서와 다르다
      (실측: '승인 건 - : 6'). y로 줄을 묶고 x로 늘어놓아야 문장이 선다.

    ■ 텍스트가 비면 OCR
      본문이 이미지로 박힌 쪽이 많다(제326차: 14쪽 중 11쪽). 한글이 거의
      없으면 그 반쪽만 OCR한다(반쪽당 약 1.1초).

      `force_ocr`는 텍스트가 **있는데 못 쓸 때** 쓴다. 굵은 글씨를 여러 번
      겹쳐 찍은 PDF는 글자가 열일곱 번씩 되풀이돼 나오는데(실측 제306·307차),
      한글 수만 보면 멀쩡해 보여 OCR로 넘어가지 않는다.
    """
    import fitz

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with fitz.open(stream=blob, filetype='pdf') as d:
            out: list[str] = []
            for page in d:
                w = page.rect.width
                cuts = ([(0.0, w / 2), (w / 2, w)] if w >= _2UP_MIN_WIDTH
                        else [(0.0, w)])
                for lo, hi in cuts:
                    txt = '' if force_ocr else _words_to_text(page, lo, hi)
                    if ocr and (force_ocr or
                                len(re.findall(r'[가-힣]', txt)) < _OCR_KO_FLOOR):
                        got = _ocr(page, lo, hi, dpi)
                        if (len(re.findall(r'[가-힣]', got))
                                > len(re.findall(r'[가-힣]', txt))):
                            txt = got
                    out.append(txt)
    return out


def _words_to_text(page, lo: float, hi: float) -> str:
    """낱말 좌표로 줄을 다시 세운다. y로 묶고 x로 늘어놓는다."""
    lines: dict[int, list[tuple[float, str]]] = {}
    for x0, y0, _x1, _y1, word, *_ in page.get_text('words'):
        if lo <= x0 < hi:
            lines.setdefault(round(y0 / 2.5), []).append((x0, word))
    return '\n'.join(' '.join(t for _, t in sorted(lines[k]))
                     for k in sorted(lines))


def _ocr(page, lo: float, hi: float, dpi: int) -> str:
    """반쪽을 그림으로 떠서 읽는다. 실패해도 수집을 멈추지 않는다."""
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning('OCR 도구가 없어 이미지 본문을 건너뜁니다')
        return ''
    try:
        pm = page.get_pixmap(dpi=dpi,
                             clip=fitz.Rect(lo, 0, hi, page.rect.height))
        img = Image.open(io.BytesIO(pm.tobytes('png')))
        # kor+eng — 본문에 LTE·MW 같은 라틴 낱말이 섞인다. kor 단독으로 읽으면
        # 'LTE'가 '11'로 뭉개진다(실측).
        return pytesseract.image_to_string(img, lang='kor+eng', config='--psm 6')
    except Exception as exc:                       # noqa: BLE001
        logger.warning('OCR 실패 (%s쪽): %s', page.number + 1, exc)
        return ''


# ══════════════════════════════════════════════════════════════════════
# 개최결과 판독 — 회차별 의결
# ══════════════════════════════════════════════════════════════════════
#: 의결 유형 머리글. 개최결과는 유형별로 안건을 묶어 싣는다.
#: **부결·보류까지 담는 것이 이 자료의 값어치**다 — 허가된 것만 세면
#: 생존 편향에 빠진다.
#:
#: ⚠️ 편집 얼개가 열 해 사이에 두 번 바뀌었다. 셋을 모두 읽어야 한다.
#:
#:   제159~215차  `<원안의결>` `<보류>`      건수 없거나 '<… : 6건>'
#:   제216~304차  `허가기준 충족(14건)`      항목이 'N.' · **사업자명이 붙는다**
#:   제305차~     `허가 (23건)`              항목이 'N.' · 지역명이 앞에 붙는다
#:
#: 가운데 얼개가 가장 알차다. '궁항해상풍력발전(주)의 해남 궁항해상풍력
#: 발전사업 허가(안)'처럼 **사업자·지역·사업명이 한 줄에** 들어 있다.
_VERDICT_HEAD = re.compile(
    r'(?:^|[\s<:])('
    r'조건부\s*허가\s*기준\s*충족|조건부\s*인가\s*기준\s*충족|'
    r'조건부\s*승인\s*기준\s*충족|'
    r'허가\s*기준\s*충족|인가\s*기준\s*충족|승인\s*기준\s*충족|'
    r'조건부\s*허가|조건부\s*인가|조건부\s*승인|'
    r'허가심의\s*보류|심의\s*보류|심의\s*연기|심의연기|재심의|'
    r'원안\s*수정의결|원안\s*의결|원안의결|수정의결|'
    r'불허가|부결|기각|철회|보류|허가|인가|승인'
    # 건수를 적는 자리가 얼개마다 다르다 — '(6건)' 이기도 하고 '<… : 6건>'
    # 이기도 하다. 어느 쪽이든 받는다.
    r')\s*(?:\(\s*(\d+)\s*건\s*\)|(?:\s*[:：]\s*(\d+)\s*건\s*)?>)')

#: 서면심사 회차는 구획을 나누지 않고 '심의결과 : 허가기준 충족' 한 줄로
#: 끝낸다(제222차 등). 구획을 못 찾았을 때만 본다 — 본문 요약에도 같은 말이
#: 나오므로 먼저 보면 안건을 두 번 세게 된다.
_VERDICT_BARE = re.compile(
    r'심의\s*결과\s*[:：]\s*('
    r'조건부\s*허가\s*기준\s*충족|허가\s*기준\s*충족|인가\s*기준\s*충족|'
    r'승인\s*기준\s*충족|'
    r'조건부\s*허가|조건부\s*인가|조건부\s*승인|'
    r'허가심의\s*보류|심의\s*보류|심의\s*연기|심의연기|'
    r'원안\s*수정의결|원안\s*의결|원안의결|불허가|부결|보류|허가|인가|승인)')

#: 건수를 적지 않고 바로 안건으로 넘어가는 회차가 있다(제301차 등).
#: 느슨하게 보되, **'허가'같은 한 낱말은 받지 않는다** — 안건명 안에도 흔해
#: 잘못 잡힌다. 구획 이름으로만 쓰이는 긴 말마디에만 한정한다.
_VERDICT_LOOSE = re.compile(
    r'(?:^|[\s<:])((?:조건부\s*)?(?:허가|인가|승인)\s*기준\s*충족|'
    r'조건부\s*(?:허가|인가|승인)|허가심의\s*보류|심의\s*보류|'
    r'심의\s*연기|불허가)(?=\s*(?:\d{1,2}\.\s|[ㅇ○●]\s))')

#: 항목. 새·가운데 얼개는 'N.', 옛 얼개는 '- '로 시작한다. HWP에서 뽑은
#: 본문은 줄바꿈 없이 이어지므로 **줄머리에 매지 않는다.**
_ITEM_NUM = re.compile(r'(?:^|[\s])(\d{1,2})\.\s+(?=\S)')
_ITEM_DASH = re.compile(r'(?:^|[\s])[-–]\s+(?=[^\s\-])')
#: 안건이 하나뿐인 회차는 번호도 줄표도 없이 'ㅇ'만 붙는다(제272·290차의
#: 한전 기본공급약관 변경인가 등). 셋 다 없으면 그 구획은 비운다.
_ITEM_CIRCLE = re.compile(r'(?:^|[\s])[ㅇ○●]\s+(?=\S)')

#: 사유·부대의견. 조건부·보류의 까닭이며, 결과보다 이쪽이 실무에 쓸모 있다.
_REASON = re.compile(r'[※*]\s*\(?\s*(?:사\s*유|부대의견)?\s*\)?\s*(.{4,})')

#: 구획 머리글이 아예 없는 회차. '심의안건 6건 모두 원안의결' 한 줄 뒤에
#: 안건이 줄줄이 붙는다(제160차 등). 이 꼴은 의결이 하나뿐이라 구획을 나눌
#: 까닭이 없었던 것이다.
_ALL_SAME = re.compile(
    r'심의안건\s*(\d+)\s*건\s*(?:은\s*)?모두\s*'
    r'(원안\s*의결|원안의결|가결|허가|인가|승인|보류|부결)')
#: 여기서 본문이 끝난다. 뒤의 '향후계획'까지 안건으로 세지 않기 위한 것이다.
_TAIL = re.compile(r'(?:□|ㅇ)?\s*(?:향후\s*계획|차기\s*|차기제)')

#: 서면심의·의결서. 회의를 열지 않고 서면으로 처리한 건은 개최결과가 아니라
#: 의결서 서식 그대로 올라온다(제258차 등). '건 명'과 '주 문'만 있고 구획이
#: 없어 앞의 어떤 규칙에도 걸리지 않는다.
_WRITTEN_ITEM = re.compile(r'건\s*명\s*[ㅇ○●]?\s*(.{5,160}?)\s*상기\s*건')
_WRITTEN_VERDICT = re.compile(
    r'(심의\s*보류|조건부\s*허가|불허가|부결|허가|인가)\s*(?:결정|함|됨)')

#: 사업자명 — 가운데 얼개는 '…(주)의 사업명' 꼴로 붙어 있다
_COMPANY = re.compile(
    r'^(?:(㈜|\(주\)|\(유\))\s*(.{2,28}?)|(.{2,28}?)(㈜|\(주\)|\(유\)|주식회사|공사))'
    r'의\s+(.+)$')
#: 이름만 앞세운 꼴. 법인 표시가 붙은 낱말 하나까지만 회사로 본다.
_COMPANY_PLAIN = re.compile(
    r'^((?:㈜|\(주\)|\(유\))\S{1,24}|\S{1,24}(?:㈜|\(주\)|\(유\)))\s+(.{5,})$')
#: 안건명 첫머리의 지역 — '사천 BHI공장 태양광 …' → 사천
_HEAD_REGION = re.compile(r'^([가-힣]{2,4})\s')

#: 에너지원 판별. 안건명에 든 낱말로 가른다.
SOURCE_PATTERNS = [
    ('SOLAR', r'태양광|태양에너지'),
    ('WIND', r'풍력'),
    ('FUELCELL', r'연료전지'),
    ('BESS', r'BESS|전기저장|에너지저장'),
    ('BIO', r'바이오|매스|SRF|고형연료'),
    ('HYDRO', r'수력|조력'),
    ('THERMAL', r'화력|복합|열병합|가스터빈|LNG|집단에너지|부생가스'),
]
#: 허가의 성격 — 선례로서 무게가 다르다.
_KIND = [('CHANGE', r'변경\s*허가|변경\s*인가|변경'), ('TRANSFER', r'양수|양도'),
         ('EXTEND', r'연장'), ('CANCEL', r'취소|철회'),
         ('NEW', r'허가\s*\(?안|인가\s*\(?안|허가안|인가안')]

#: 의결 유형을 한 꼴로 맞춘다. 얼개마다 부르는 이름이 달라도 뜻은 같다.
VERDICT_ALIAS = {
    '허가기준충족': '허가', '인가기준충족': '인가', '승인기준충족': '승인',
    '조건부허가기준충족': '조건부 허가', '조건부인가기준충족': '조건부 인가',
    '조건부승인기준충족': '조건부 승인',
    '조건부허가': '조건부 허가', '조건부인가': '조건부 인가',
    '조건부승인': '조건부 승인',
    '허가심의보류': '심의 보류', '심의보류': '심의 보류',
    '심의연기': '심의 연기', '원안의결': '원안의결',
    '원안수정의결': '수정의결', '수정의결': '수정의결',
}
#: 판정을 네 갈래로 묶는다. 사례를 셀 때 쓰는 축이다.
#: '원안의결'은 옛 얼개에서 허가·인가를 가리지 않고 함께 쓰던 말이라
#: 어느 쪽인지 알 수 없다. 그래서 가결로만 묶고 종류는 비워 둔다.
VERDICT_GROUP = {
    '허가': '가결', '인가': '가결', '승인': '가결', '원안의결': '가결',
    '수정의결': '가결',
    '조건부 허가': '조건부', '조건부 인가': '조건부', '조건부 승인': '조건부',
    '심의 보류': '보류', '심의 연기': '보류', '보류': '보류', '재심의': '보류',
    '불허가': '부결', '부결': '부결', '기각': '부결', '철회': '부결',
}


def read_result(blob: bytes,
                round_no: int | None = None) -> tuple[str, list[dict]]:
    """
    개최결과 첨부 → (본문, 안건 목록). **비면 OCR로 한 번 더 본다.**

    최근 회차 일부는 PDF인데 본문이 이미지로 박혀 있다(실측 제306차: 4쪽에
    이미지 369개, 뽑히는 한글 248자). 텍스트만 읽으면 안건이 통째로 사라지고
    **그 회차는 아무 일도 없었던 것처럼 보인다.** 조용히 비는 쪽이 틀린
    값보다 위험하므로, 안건이 하나도 안 나오면 그림으로 떠서 다시 읽는다.
    """
    text = read_document(blob)
    rows = parse_result(text, round_no)
    if not rows and blob[:4] == b'%PDF':
        text = '\n'.join(read_pages(blob, ocr=True, force_ocr=True))
        rows = parse_result(text, round_no)
    return text, rows


def parse_result(text: str, round_no: int | None = None) -> list[dict]:
    """
    개최결과 → 안건 목록.

    → [{'round','verdict','group','no','title','company','source','kind',
        'region','reason'}]

    **부결·보류도 그대로 담는다.** 허가된 것만 모으면 "이 지역은 다 된다"는
    잘못된 그림이 만들어진다. 왜 보류됐는지(사업이행 가능성·부지확보·계통
    보강 등)가 허가 사실보다 쓸모 있는 경우가 많다.
    """
    if round_no is None:
        m = ROUND_RE.search(text)
        round_no = int(m.group(1)) if m else None

    # 심의내용 앞의 '심의결과 요약'에도 같은 머리글이 나온다. 요약을 안건으로
    # 세면 같은 건이 두 번 잡히므로, 본문이 시작되는 곳부터 읽는다.
    body_at = 0
    for mark in ('심의내용', '심의 내용'):
        k = text.find(mark)
        if k > 0:
            body_at = k
            break
    body = text[body_at:]

    heads = (list(_VERDICT_HEAD.finditer(body))
             or list(_VERDICT_LOOSE.finditer(body)))
    out: list[dict] = []
    for i, h in enumerate(heads):
        verdict = _norm_verdict(h.group(1))
        seg = body[h.end(): heads[i + 1].start() if i + 1 < len(heads)
                   else len(body)]
        for no, raw in _items(seg):
            row = _row(round_no, verdict, no, raw)
            if row:
                out.append(row)
    if out:
        return out

    # 서면심의·의결서 — '건 명'과 '주 문'뿐이다.
    wm = _WRITTEN_ITEM.search(body)
    if wm:
        vm = _WRITTEN_VERDICT.search(body[wm.end():wm.end() + 600])
        row = _row(round_no, _norm_verdict(vm.group(1)) if vm else '', 1,
                   wm.group(1))
        if row:
            rs = _REASON.search(body[wm.end():])
            if rs:
                row['reason'] = re.sub(r'\s+', ' ', rs.group(1)).strip()[:400]
            row['group'] = VERDICT_GROUP.get(row['verdict'], '')
            return [row]

    # 서면심사 회차 — 구획 없이 '심의결과 : 허가기준 충족' 한 줄뿐이다.
    m = _VERDICT_BARE.search(body)
    if m:
        seg = body[m.end():]
        tail = _TAIL.search(seg)
        if tail:
            seg = seg[:tail.start()]
        verdict = _norm_verdict(m.group(1))
        out = [r for no, raw in _items(seg)
               if (r := _row(round_no, verdict, no, raw))]
        if out:
            return out

    # 구획 머리글이 없는 회차 — 안건이 모두 같은 결론이라 나누지 않은 것이다.
    m = _ALL_SAME.search(body)
    if m:
        seg = body[m.end():]
        tail = _TAIL.search(seg)
        if tail:
            seg = seg[:tail.start()]
        verdict = _norm_verdict(m.group(2))
        out = [r for no, raw in _items(seg)
               if (r := _row(round_no, verdict, no, raw))]
    return out


def _items(seg: str) -> list[tuple[int, str]]:
    """구획 안의 항목을 잘라 낸다. 'N.'을 먼저 보고, 없으면 '- ', 'ㅇ' 차례."""
    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in _ITEM_NUM.finditer(seg)]
    for pat in (_ITEM_DASH, _ITEM_CIRCLE):
        if marks:
            break
        marks = [(m.start(), m.end(), n + 1)
                 for n, m in enumerate(pat.finditer(seg))]
    out: list[tuple[int, str]] = []
    for k, (_st, en, no) in enumerate(marks):
        stop = marks[k + 1][0] if k + 1 < len(marks) else len(seg)
        out.append((no, seg[en:stop]))
    return out


def _row(round_no, verdict, no, raw: str) -> dict | None:
    """항목 한 덩이 → 안건. 사유는 본문 뒤에 붙어 있다."""
    reason = ''
    rm = _REASON.search(raw)
    if rm:
        reason = re.sub(r'\s+', ' ', rm.group(1)).strip()[:400]
        raw = raw[:rm.start()]
    title = _clean_title(re.sub(r'\s+', ' ', raw))
    if not title:
        return None

    company = ''
    cm = _COMPANY.match(title)
    if cm:
        # '㈜태안해상풍력의 …' 과 '궁항해상풍력발전(주)의 …' 두 꼴을 함께 받는다
        company = (cm.group(2) or cm.group(3) or '').strip()
        if cm.group(1) or cm.group(4):
            company = f'㈜{company}' if cm.group(1) else f'{company}{cm.group(4)}'
        title = cm.group(5).strip()
    else:
        # 옛 얼개는 '의' 없이 이름만 앞세운다 — '(주)포에버 태양광발전사업 허가(안)'
        pm = _COMPANY_PLAIN.match(title)
        if pm:
            company, title = pm.group(1).strip(), pm.group(2).strip()

    rg = _HEAD_REGION.match(title)
    return {
        'round': round_no, 'verdict': verdict,
        'group': VERDICT_GROUP.get(verdict, ''),
        'no': no, 'title': title, 'company': company,
        'source': source_of(title), 'kind': _kind_of(title),
        'region': rg.group(1) if rg else '', 'reason': reason,
    }


def _norm_verdict(s: str) -> str:
    """띄어쓰기와 옛 이름을 한 꼴로 맞춘다."""
    k = re.sub(r'\s+', '', s)
    return VERDICT_ALIAS.get(k, k)


def _clean_title(s: str) -> str:
    """안건명 정리 — 빈 괄호·중복 공백·꼬리 기호를 걷어낸다."""
    s = re.sub(r'\(\s*\)', '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip(' ·,')
    s = re.sub(r'[\s·,]*$', '', s)
    if len(s) < 5 or s.startswith('※'):   # 사유·머리글이 안건으로 잡히는 것 방지
        return ''
    return s[:200]


def source_of(text: str) -> str:
    for code, pat in SOURCE_PATTERNS:
        if re.search(pat, text):
            return code
    return 'OTHER'


def _kind_of(title: str) -> str:
    for code, pat in _KIND:
        if re.search(pat, title):
            return code
    return 'OTHER'


# ══════════════════════════════════════════════════════════════════════
# 회의록 판독 — 안건별 심의 내용
# ══════════════════════════════════════════════════════════════════════
#: 회의록의 의결 유형 구획은 **개최결과와 같다.** 앞에 일련번호가 붙기도
#: 하고('3. 인가 (11건)') 안 붙기도 하는데('인가기준 충족 (3건)'),
#: `_VERDICT_HEAD`가 앞에 공백만 요구하므로 둘 다 걸린다.
_MIN_SECTION = _VERDICT_HEAD

#: 안건명이 여기서 끝나고 심의 내용이 시작된다. HWP 회의록은 줄바꿈 없이
#: 죽 이어지므로 줄 단위로는 가를 수 없다.
#: `《 개요 》`·`《 위원 발언요지 》`도 내용의 시작이다.
_MIN_BODY_AT = re.compile(r'[ㅇ○●※《]|\n')


def read_minutes(blob: bytes) -> list[str]:
    """
    회의록 첨부 → 논리 쪽 목록. **형식은 첫 바이트로 가른다.**

    회의록도 회차에 따라 다르다(실측 165건 중 HWP 142 · PDF 23). HWP는
    글자가 그대로 들어 있어 바로 읽히고, PDF만 2-up 분할과 OCR이 필요하다.
    HWP를 PDF로 열려 하면 `unknown image file format`으로 조용히 빈 쪽이
    되어 **그 회차가 통째로 사라진다.**
    """
    if blob[:4] == b'%PDF':
        return read_pages(blob, ocr=True)
    return [read_document(blob)]


def parse_minutes(pages: list[str], round_no: int | None = None) -> list[dict]:
    """
    회의록 → 안건별 심의 내용.

    → [{'round','verdict','group','no','title','source','text'}]

    개최결과가 '무엇이 의결됐는가'라면 회의록은 '왜 그렇게 됐는가'다. 조건부
    허가의 실제 조건, 보류 사유, 위원이 지적한 쟁점, 그리고 개최결과에는 없는
    **사업주체·최대주주·지번·설비용량·총사업비**가 여기 있다.

    **개최결과와 같은 구획 얼개**(의결 유형 → 연번)를 쓰므로 `회차+유형+연번`
    으로 두 자료를 정확히 이을 수 있다. 회의록 쪽은 OCR을 거쳐 글자가 조금씩
    틀어지므로, 잇는 열쇠를 안건명이 아니라 이 세 값으로 잡는다.

    ⚠️ 회의록에는 같은 구획이 **두 번** 나온다 — 앞머리 'Ⅱ 상정 안건'의 목록과
       뒤쪽 'Ⅳ 심의안건'의 본문이다. 앞엣것에는 사유가, 뒤엣것에는 사업주체·
       지번·용량이 실린다. 둘 다 담고, 이어 붙일 때 합친다(`join_minutes`).
    """
    body = re.sub(r'\n{2,}', '\n', '\n'.join(pages))
    if round_no is None:
        m = ROUND_RE.search(body)
        round_no = int(m.group(1)) if m else None

    heads = list(_MIN_SECTION.finditer(body))
    out: list[dict] = []
    for i, h in enumerate(heads):
        verdict = _norm_verdict(h.group(1))
        seg = body[h.end(): heads[i + 1].start() if i + 1 < len(heads)
                   else len(body)]
        # 개최결과와 같은 항목 자르기를 쓴다. HWP 회의록은 줄바꿈 없이 죽
        # 이어지므로 줄 단위로 가르면 안건이 통째로 뭉친다.
        for no, raw in _items(seg):
            row = _min_row(round_no, verdict, no, raw)
            if row:
                out.append(row)
    return out


def _min_row(round_no, verdict, no, raw: str) -> dict | None:
    """항목 한 덩이 → 안건명 + 심의 내용."""
    m = _MIN_BODY_AT.search(raw)
    title = _clean_title(re.sub(r'\s+', ' ', raw[:m.start()] if m else raw))
    if not title:
        return None
    detail = re.sub(r'[ \t]{2,}', ' ', raw[m.start():].strip()) if m else ''
    if len(detail) > 5000:      # 안건 경계를 놓친 경우 방어. 앞부분이 알맹이다.
        detail = detail[:5000] + ' …(생략)'
    return {
        'round': round_no, 'verdict': verdict,
        'group': VERDICT_GROUP.get(verdict, ''),
        'no': no, 'title': title, 'source': source_of(title), 'text': detail,
    }


def join_minutes(results: list[dict], minutes: list[dict]) -> int:
    """
    개최결과 안건에 회의록 심의 내용을 붙인다. → 이어 붙인 건수

    ■ 왜 안건명으로 잇지 않는가
      회의록 본문은 이미지라 OCR을 거친다. 글자가 조금씩 틀어지므로
      ('LTE'→'11', '㈜'→'수') 이름을 열쇠로 삼으면 멀쩡한 짝을 놓친다.
      구획 얼개가 두 자료에 똑같이 있으니 **회차+의결유형+연번**이 더 단단하다.

    ■ 그래도 확인은 한다
      번호만 믿으면 편집이 다른 회차에서 엉뚱한 안건이 붙는다. 이름의
      낱말이 절반도 겹치지 않으면 잇지 않고 비워 둔다. **틀리게 잇느니
      비는 편이 낫다** — 사례 대조는 사람이 읽고 판단하는 자료다.

    ■ 같은 안건이 회의록에 두 번 나온다
      앞머리 '상정 안건' 목록에는 **사유**가, 뒤쪽 '심의안건' 본문에는
      **사업주체·지번·용량·발언요지**가 실린다. 어느 한쪽만 쓰면 나머지를
      잃으므로 **둘을 이어 붙인다.**
    """
    idx: dict[tuple, list[dict]] = {}
    for m in minutes:
        idx.setdefault((m['round'], m['verdict'], m['no']), []).append(m)

    hit = 0
    for r in results:
        cand = [m for m in idx.get((r['round'], r['verdict'], r['no']), [])
                if _same_title(r['title'], m['title'])]
        # 같은 내용이 두 번 실리기도 한다. 긴 것부터 담되 이미 담은 것에
        # 들어 있는 조각은 건너뛴다.
        merged: list[str] = []
        for t in sorted({m['text'] for m in cand if m['text']},
                        key=len, reverse=True):
            if not any(t in kept for kept in merged):
                merged.append(t)
        r['minutes'] = '\n\n'.join(merged)
        if r['minutes']:
            hit += 1
    return hit


def _same_title(a: str, b: str) -> bool:
    """OCR 오차를 견디는 이름 대조 — 두 글자 조각이 절반 넘게 겹치면 같다고 본다."""
    def grams(s):
        s = re.sub(r'[^가-힣A-Za-z0-9]', '', s)
        return {s[i:i + 2] for i in range(len(s) - 1)}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return False
    return len(ga & gb) / min(len(ga), len(gb)) >= 0.5


# ══════════════════════════════════════════════════════════════════════
# 허가취소 공고 판독 — 허가받고도 좌초한 사업
# ══════════════════════════════════════════════════════════════════════
"""
허가대장에는 **허가된 것만** 남는다. 그래서 대장만 보면 "허가만 받으면
된다"는 그림이 된다. 실제로는 준비기간 안에 착공하지 못해 **취소되는 사업이
회차마다 수십 건**이다. 그것이 여기 있다.
"""
#: 처분과 청문은 뜻이 전혀 다르다.
#:   처분 공고 — 이미 취소됐다(확정)
#:   청문 공고 — 취소하겠다고 알리는 단계. 소명하면 살아남기도 한다
#: 섞으면 취소 건수가 부풀려진다.
_CANCEL_KIND = [('HEARING', r'청문'), ('DISPOSAL', r'취소\s*처분|허가\s*취소')]

#: 표 한 줄의 끝자락 — 설비용량·허가번호·처분사유가 나란히 온다. 본문이
#: 줄바꿈 없이 이어지므로 **이 셋을 닻으로 삼아** 행을 가른다.
#:
#: ⚠️ 용량에 단위가 붙기도 하고('93.15MW') 열 이름에만 붙기도 한다
#:    ('설비용량(MW)' 아래 '10.8'). 그래서 단위를 반드시 요구하지 않는다.
#:    대신 **처분사유가 뒤따르는지 확인**한다 — 이 조건이 없으면 주소의
#:    지번('1104-2번지')을 허가번호로 잘못 읽는다.
#:    허가번호도 '2019-78'과 '제2019-78호' 두 꼴로 적힌다.
_CANCEL_ANCHOR = re.compile(
    r'(\d{1,5}(?:\.\d+)?)\s*(?:MW|㎿)?\s+제?\s*((?:19|20)\d\d\s*-\s*\d{1,4})\s*호?\s+'
    r'(?=전기사업법|상동)')

#: 사업이 하나뿐인 처분 공고는 처분사유 칸 없이 허가일만 적는다.
#:   `49.8MW 2019년10월30일 (제2019-112호)`
#: 처분사유를 닻으로 쓸 수 없으므로 **단위(MW)와 괄호 친 허가번호**를 함께
#: 요구한다. 둘 다 있어야 지번을 허가번호로 잘못 읽지 않는다.
_CANCEL_ANCHOR_NOREASON = re.compile(
    r'(\d{1,5}(?:\.\d+)?)\s*(?:MW|㎿)\s+(?:(?:19|20)\d\d년\s*\d{1,2}월\s*\d{1,2}일\s*)?'
    r'\(\s*제?\s*((?:19|20)\d\d\s*-\s*\d{1,4})\s*호?\s*\)')

#: 청문 실시 공고는 표가 아니라 **당사자별 쪽**으로 되어 있고 설비용량이 없다.
#:   `당사자2 허가번호 발전사업명 발전사업자 지역
#:    2018-60 서순천풍력 발전사업 서순천풍력발전㈜ 전남 순천시 …
#:    예정된 처분 발전사업 허가 취소  처분사유 …`
#: 취소가 **확정된 것이 아니라 예고된** 단계라 처분 공고와 반드시 갈라 둔다.
_HEARING_ROW = re.compile(
    r'허가번호\s*발전\s*사업명\s*발전사업자\s*지역\s*'
    r'((?:19|20)\d\d\s*-\s*\d{1,4})\s+(.+?)\s*예정된\s*처분', re.S)
_HEARING_REASON = re.compile(r'처분\s*사유\s*(.+?)\s*(?:청문\s*실시|$)', re.S)

#: 청문 공고의 또 다른 서식 — 사업마다 쪽을 나누고 **항목 이름을 붙여** 적는다.
#:   `⑴ 삼척 평산풍력 발전사업  허가증 번호 2022-82 … 상호 블랙스톰㈜ …
#:    사업장소 강원도 삼척시 도계읍 황조리 산159번지 일원
#:    원동력의 종류 풍력  설비용량 20MW`
#: 세 서식 가운데 **가장 자세하다** — 원동력과 설비용량이 따로 적힌다.
_HEARING_BLOCK = re.compile(r'[⑴-⒇①-⑳]\s*(.{4,80}?발전\s*사업)\s')
_HEARING_FIELDS = {
    'permit_no': re.compile(r'허가증\s*번호\s*((?:19|20)\d\d\s*-\s*\d{1,4})'),
    'company': re.compile(r'상\s*호\s*(.+?)\s*(?:성\s*명|대표자|사업장소)'),
    'location': re.compile(r'사업\s*장소\s*(.+?)\s*(?:원동력|설비\s*용량|예정된)'),
    'source_raw': re.compile(r'원동력의?\s*종류\s*(.+?)\s*(?:설비\s*용량|공사계획)'),
    'capacity': re.compile(r'설비\s*용량\s*([\d,]+(?:\.\d+)?)\s*(?:MW|㎿)'),
    'reason': re.compile(r'(?:취소|처분)\s*사유\s*(.+?)\s*(?:청문|$)', re.S),
}
#: 사업명은 반드시 '발전사업'으로 끝나고 **맨 앞에 온다.** 사업자명에도
#: '발전'이 들어가므로(묘도연료전지발전) 뒤에서 찾으면 안 된다.
_CANCEL_NAME_END = re.compile(r'발전\s*사업')
#: 사업장소는 시·도로 시작한다. 사업자에 법인 표시가 없는 공고가 많아
#: ('국제해양에너지') 법인 표시만으로는 사업자와 장소를 가를 수 없다.
_CANCEL_LOC = re.compile(
    r'(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|'
    r'경북|경남|제주|충청[남북]|전라[남북]|경상[남북])'
    r'(?:특별자치도|특별자치시|특별시|광역시|도|시)?\s')
#: 처분 근거 조문. 표에서 **처분사유 칸의 값이자, 다음 행 사업명 앞의 찌꺼기**다.
#: 한 꼴로 잡아 두 군데에 함께 쓴다 — 경계를 정확히 그어야 사업명이 안 밀린다.
_LAW_CITE = re.compile(
    r'전기사업법\s*제\s*\d+\s*조'
    r'(?:\s*제\s*\d+\s*항)?'
    r'(?:\s*제\s*\d+(?:\s*의\s*\d+)?\s*호)?'
    r'(?:\s*(?:및|,)\s*제\s*\d+(?:\s*의\s*\d+)?\s*호)*')
#: 처분일자·근거 회차
_CANCEL_DATE = re.compile(r'처분\s*일자\s*[:：]?\s*((?:19|20)\d\d)\s*[.년]\s*'
                          r'(\d{1,2})\s*[.월]\s*(\d{1,2})')
_CANCEL_ROUND = re.compile(r'제\s*(\d{2,4})\s*차\s*전기위원회')


def parse_cancellation(text: str, title: str = '') -> list[dict]:
    """
    허가취소 공고 → 취소된 사업 목록.

    → [{'kind','name','company','location','sido','sigungu','source',
        'capacity_mw','permit_no','reason','disposed_on','round'}]

    ■ 왜 뒤에서부터 가르는가
      HWPX 본문은 표가 줄바꿈 없이 이어져 나온다. 앞에서부터 사업명을 찾으려
      하면 장소에 든 낱말과 뒤섞인다. 반면 **`93.15MW 2018-95`처럼 용량과
      허가번호가 나란히 오는 자리**는 표마다 어김없이 한 번씩만 나온다.
      그 자리를 닻으로 잡고 앞의 덩이를 사업명·사업자·장소로 나눈다.
    """
    # 제목이 줄여져 '청문'이 잘려 나가기도 한다. 본문 머리도 함께 본다.
    kind = 'DISPOSAL'
    for code, pat in _CANCEL_KIND:
        if re.search(pat, f'{title} {text[:400]}'):
            kind = code
            break

    dm = _CANCEL_DATE.search(text)
    disposed = (f'{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}'
                if dm else '')
    rm = _CANCEL_ROUND.search(text)
    round_no = int(rm.group(1)) if rm else None

    body = re.sub(r'\s+', ' ', text)
    if kind == 'HEARING':
        return _parse_hearing(body, disposed, round_no)

    anchors = (list(_CANCEL_ANCHOR.finditer(body))
               or list(_CANCEL_ANCHOR_NOREASON.finditer(body)))
    out: list[dict] = []
    prev = 0
    for i, a in enumerate(anchors):
        head = body[prev:a.start()]
        prev = a.end()
        # 사유는 다음 닻 앞까지 — '상동'이면 바로 위 사업과 같다
        tail = body[a.end(): anchors[i + 1].start() if i + 1 < len(anchors)
                    else min(len(body), a.end() + 60)]
        reason = _cancel_reason(tail, out)

        name, company, loc = _cancel_split(head)
        if len(name) < 4:
            continue

        sido, sigungu = split_region(loc)
        out.append({
            'kind': kind, 'name': name, 'company': company, 'location': loc,
            'sido': sido, 'sigungu': sigungu, 'source': source_of(name),
            'capacity_mw': _mw(a.group(1)),
            'permit_no': re.sub(r'\s+', '', a.group(2)),
            'reason': reason, 'disposed_on': disposed, 'round': round_no,
        })
    return out


def _parse_hearing(body: str, disposed: str, round_no: int | None) -> list[dict]:
    """
    청문 실시 공고 → 취소가 **예고된** 사업 목록.

    설비용량이 실리지 않아 `capacity_mw`는 비운다. 0으로 채우면 "작은
    사업"으로 잘못 읽힌다.
    """
    out: list[dict] = []
    for m in _HEARING_ROW.finditer(body):
        name, company, loc = _cancel_split(m.group(2))
        if len(name) < 4:
            continue
        rm = _HEARING_REASON.search(body[m.end(): m.end() + 700])
        sido, sigungu = split_region(loc)
        out.append({
            'kind': 'HEARING', 'name': name, 'company': company,
            'location': loc, 'sido': sido, 'sigungu': sigungu,
            'source': source_of(name), 'capacity_mw': None,
            'permit_no': re.sub(r'\s+', '', m.group(1)),
            'reason': re.sub(r'\s+', ' ', rm.group(1)).strip()[:200] if rm else '',
            'disposed_on': disposed, 'round': round_no,
        })
    return out or _parse_hearing_labeled(body, disposed, round_no)


def _parse_hearing_labeled(body: str, disposed: str,
                           round_no: int | None) -> list[dict]:
    """항목 이름이 붙은 청문 공고. 원동력·설비용량까지 그대로 적혀 있다."""
    blocks = list(_HEARING_BLOCK.finditer(body))
    out: list[dict] = []
    for i, b in enumerate(blocks):
        seg = body[b.end(): blocks[i + 1].start() if i + 1 < len(blocks)
                   else len(body)]
        got = {k: (m.group(1).strip() if (m := pat.search(seg)) else '')
               for k, pat in _HEARING_FIELDS.items()}
        if not got['permit_no']:      # 머리말이 잘못 잡힌 것이다
            continue
        name = b.group(1).strip(' ·,|')
        sido, sigungu = split_region(got['location'])
        out.append({
            'kind': 'HEARING', 'name': name, 'company': got['company'],
            'location': got['location'], 'sido': sido, 'sigungu': sigungu,
            # 원동력이 따로 적혀 있으면 그쪽이 더 미덥다
            'source': source_of(got['source_raw'] or name),
            'capacity_mw': _mw(got['capacity']),
            'permit_no': re.sub(r'\s+', '', got['permit_no']),
            'reason': re.sub(r'\s+', ' ', got['reason'])[:200],
            'disposed_on': disposed, 'round': round_no,
        })
    return out


def _cancel_split(head: str) -> tuple[str, str, str]:
    """
    한 행의 앞부분 → (사업명, 사업자, 사업장소).

    **차례가 열쇠다.** 사업명이 먼저 오고 반드시 '발전사업'으로 끝나므로
    거기서 한 번 자른다. 남은 것에서 시·도가 나오는 자리가 장소의 시작이고,
    그 앞이 사업자다.

    사업자명을 법인 표시(㈜)로 찾지 않는 까닭은, 표시 없이 상호만 적은 공고가
    많기 때문이다('국제해양에너지', '동도에너지'). 반대로 사업명 뒤에서
    '발전'을 찾으면 사업자명에 든 '발전'에 걸린다('묘도연료전지발전').
    """
    s = _cancel_name(head)
    m = _CANCEL_NAME_END.search(s)
    if not m:
        return s.strip(' ·,|'), '', ''
    name, rest = s[:m.end()].strip(' ·,|'), s[m.end():].strip(' ·,|')
    lm = _CANCEL_LOC.search(rest)
    if not lm:
        return name, rest, ''
    return name, rest[:lm.start()].strip(' ·,|'), rest[lm.start():].strip(' ·,|')


def _cancel_name(s: str) -> str:
    """
    사업명에서 **앞 행의 꼬리**를 걷어낸다.

    표가 한 줄로 이어져 나오므로, 한 행의 사업명 앞에는 바로 앞 행의 처분사유
    ('상동' 또는 '전기사업법 제12조제1항 제2호')가 그대로 붙어 온다. 첫 행
    앞에는 표 머리글이 통째로 붙는다.
    """
    k = s.rfind('처분사유')            # 표 머리글까지는 통째로 버린다
    if k >= 0:
        s = s[k + 4:]
    s = re.sub(r'^\s*상동\s*', '', s)
    m = _LAW_CITE.match(s.lstrip())
    if m:
        s = s.lstrip()[m.end():]
    return s.strip(' ·,|')


def _cancel_reason(tail: str, prev_rows: list[dict]) -> str:
    """'상동'은 바로 위 사업과 같은 사유라는 뜻이다. 그대로 두면 읽을 수 없다."""
    t = re.sub(r'\s+', ' ', tail).strip(' ·,|')
    if t.startswith('상동'):
        return prev_rows[-1]['reason'] if prev_rows else '상동'
    m = _LAW_CITE.match(t)
    # 조문이 아니면 다음 사업명이 이어진 것이다. 어림잡아 자르지 않고 비운다.
    return m.group(0).strip() if m else ''


# ══════════════════════════════════════════════════════════════════════
# 허가대장 판독 — 3MW 초과 발전사업 허가현황
# ══════════════════════════════════════════════════════════════════════
#: 허가대장의 열 차례. 쪽마다 표 머리가 되풀이된다.
#:   NO(연도) | 번호 | 상호(사업자) | 발전소위치 | 원동력 | 용량(MW)
#:   | 허가·변경일 | 준비기간 | 기타(변경사항)
_REG_DATE = re.compile(r'^(19|20)\d\d[-.]\d{1,2}(?:[-.]\d{1,2})?$')
_REG_YEAR = re.compile(r'^(19|20)\d\d$')

#: 시·도. **긴 이름부터** 본다 — '강원특별자치도'를 '강원도'보다 먼저 보지
#: 않으면 앞 세 글자만 떼고 '특별자치도'가 남는다.
#:
#: 이름을 한 꼴로 모으는 까닭은, 같은 삼척시가 '강원도'와 '강원특별자치도'로
#: 갈려 두 줄로 세어지기 때문이다(2023년 개편). 사례를 셀 때 한 지역이
#: 둘로 쪼개지면 "선례가 적다"는 잘못된 그림이 된다.
_SIDO_TABLE = [
    ('강원특별자치도', '강원'), ('전북특별자치도', '전북'),
    ('제주특별자치도', '제주'), ('세종특별자치시', '세종'),
    ('서울특별시', '서울'), ('부산광역시', '부산'), ('대구광역시', '대구'),
    ('인천광역시', '인천'), ('광주광역시', '광주'), ('대전광역시', '대전'),
    ('울산광역시', '울산'),
    ('충청북도', '충북'), ('충청남도', '충남'), ('전라북도', '전북'),
    ('전라남도', '전남'), ('경상북도', '경북'), ('경상남도', '경남'),
    ('경기도', '경기'), ('강원도', '강원'), ('제주도', '제주'),
    ('서울시', '서울'), ('부산시', '부산'), ('대구시', '대구'),
    ('인천시', '인천'), ('광주시', '광주'), ('대전시', '대전'),
    ('울산시', '울산'), ('세종시', '세종'),
    ('서울', '서울'), ('부산', '부산'), ('대구', '대구'), ('인천', '인천'),
    ('광주', '광주'), ('대전', '대전'), ('울산', '울산'), ('세종', '세종'),
    ('경기', '경기'), ('강원', '강원'), ('충북', '충북'), ('충남', '충남'),
    ('전북', '전북'), ('전남', '전남'), ('경북', '경북'), ('경남', '경남'),
    ('제주', '제주'),
]
#: 시·도를 떼어낸 **뒤** 첫 시·군·구를 잡는다. 떼지 않으면 '경북영덕군'처럼
#: 도명까지 삼킨다(대장에는 공백 없이 붙여 쓴 주소가 섞여 있다).
_SIGUNGU = re.compile(r'^([가-힣]{2,4}(?:시|군|구))')


def split_region(location: str) -> tuple[str, str]:
    """'발전소위치' → (시·도, 시·군·구). 못 가르면 빈 값을 준다."""
    s = re.sub(r'\s+', '', location or '')
    sido = ''
    for pat, name in _SIDO_TABLE:
        if s.startswith(pat):
            sido, s = name, s[len(pat):]
            break
    m = _SIGUNGU.match(s)
    return sido, m.group(1) if m else ''


def parse_register(blob: bytes) -> list[dict]:
    """
    3MW 초과 발전사업 허가대장 → 허가 이력.

    → [{'year','no','company','location','sido','sigungu','source','source_raw',
        'capacity_mw','permit_date','ready_until','changed'}]

    ■ 한 사업이 여러 행일 수 있다
      대장은 **허가와 그 뒤의 변경을 모두** 싣는다. '기타(변경사항)'가 비면
      최초 허가, 내용이 있으면 그 뒤의 변경이다(대장 범례). 사업 수를 셀
      때는 최초 허가만 세야 하고, 준비기간 연장이 잦은 사업을 보려면 변경
      행을 봐야 한다. 그래서 **행을 합치지 않고 그대로 둔다.**

    ■ 열 개수가 들쭉날쭉하다
      같은 표인데 셀 병합 때문에 9열로도 17열로도 뽑힌다(실측 9열 1,943행 /
      17열 2,541행). 그래서 앞 7열만 자리로 읽고, **나머지는 날짜꼴이면
      준비기간, 아니면 변경사항**으로 가른다.
    """
    import fitz

    rows: list[dict] = []
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        with fitz.open(stream=blob, filetype='pdf') as d:
            for page in d:
                for tb in page.find_tables().tables:
                    for raw in tb.extract():
                        r = [(c or '').replace('\n', ' ').strip() for c in raw]
                        if len(r) < 9 or not _REG_YEAR.match(r[0]):
                            continue
                        rest = [c for c in r[7:] if c]
                        loc = _squeeze(r[3])
                        sido, sigungu = split_region(loc)
                        rows.append({
                            'year': r[0], 'no': r[1],
                            # 표 폭에 맞춰 회사명이 '한국농어촌공 사'처럼 끊긴다
                            'company': _squeeze(r[2]),
                            'location': loc, 'sido': sido, 'sigungu': sigungu,
                            'source': source_of(r[4]),
                            'source_raw': _squeeze(r[4]),
                            'capacity_mw': _mw(r[5]),
                            'permit_date': _norm_date(r[6]),
                            'ready_until': next((_norm_date(c) for c in rest
                                                 if _REG_DATE.match(c)), ''),
                            'changed': ' / '.join(c for c in rest
                                                  if not _REG_DATE.match(c)),
                        })
    return rows


def _squeeze(s: str) -> str:
    """표 폭에 맞춰 낱말 안에 끼어든 공백을 없앤다 ('한국농어촌공 사')."""
    s = re.sub(r'\s+', ' ', s).strip()
    return re.sub(r'(?<=[가-힣])\s+(?=[가-힣])', '', s) if len(s) <= 24 else s


def _mw(s: str) -> float | None:
    m = re.search(r'[\d,]+(?:\.\d+)?', s or '')
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def _norm_date(s: str) -> str:
    m = re.search(r'((?:19|20)\d\d)[-.](\d{1,2})(?:[-.](\d{1,2}))?', s or '')
    if not m:
        return ''
    y, mo, dd = m.group(1), int(m.group(2)), m.group(3)
    return f'{y}-{mo:02d}-{int(dd):02d}' if dd else f'{y}-{mo:02d}'
