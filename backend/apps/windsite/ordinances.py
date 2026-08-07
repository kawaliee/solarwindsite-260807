"""
지자체 이격거리 조례 수집·대조 서비스
---------------------------------------------------------------
자치법규 OPEN API로 조례 원문을 받아 풍력 이격거리 조항을 추출한다.

관리 커맨드(`sync_ordinances`)와 **검토 실행 중 자동 수집**이 같은 로직을 쓰도록
여기에 모았다. 종전에는 커맨드 안에만 있어서, 조례가 미등록된 지자체를 검토하면
'지자체 이격거리 조례'와 '정온시설 이격'이 함께 UNKNOWN으로 남았다.

⚠️ 왜 원문 대조인가
   시드값은 2차 자료(언론보도 등) 기반이었다. 화순군을 원문 대조한 결과 DB의
   1,200m가 아니라 **2,000m(10호 이상 취락)/1,500m(10호 미만)** 였다.
   기준이 틀리면 동심원 분석 결과가 통째로 틀어진다.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date

from django.core.cache import cache
from django.db import transaction

from . import lawapi

logger = logging.getLogger(__name__)

#: 조례명 후보 — 지자체마다 제명이 다르다 (도시계획 / 군계획 / 도시·군계획)
ORDINANCE_KEYWORDS = ('도시계획 조례', '군계획 조례', '도시·군계획 조례', '도시군계획 조례')

#: 거리 수치 표현. '2,000미터' / '2000m' / '1.5킬로미터'
_DIST = r'([0-9][0-9,\.]*)\s*(미터|m|M|킬로미터|km|KM)'

#: 단서 괄호 — '(단, 군도는 500미터)', '(5호 미만 … 1,500미터)'
_PAREN = re.compile(r'[(（]([^()（）]*)[)）]')

#: 이격 대상 판별 — LocalOrdinance.TARGET_CHOICES 로 매핑
#: 조례 원문은 띄어쓰기가 제각각이라('주거밀집' / '주거 밀집') \s* 를 넣어둔다.
#: 청도군 '주거 밀집지역'이 공백 하나 때문에 OTHER로 빠지던 것을 바로잡은 것이다.
TARGET_PATTERNS: list[tuple[str, str]] = [
    ('RESIDENTIAL', r'(취락|주거\s*밀집|주거\s*지역|주택|가구|세대|호\s*이상|호\s*미만)'),
    ('QUIET_FACILITY', r'(정온\s*시설|학교|병원|요양|어린이집|경로당|공공\s*시설)'),
    ('ROAD', r'(도로|국도|지방도|군도|고속도로|교통\s*시설)'),
    ('RAILWAY', r'(철도|역사)'),
]

#: 자동 수집 실패 기록 유지 시간 — 조례가 없는 지자체를 매 검토마다 다시 조회하지 않는다
_MISS_TTL = 60 * 60 * 24
#: 같은 지자체를 두 프로바이더가 동시에 조회하지 않도록 직렬화한다
#: (LocalOrdinanceProvider·QuietFacilityProvider가 스레드풀에서 나란히 돈다)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


# ======================================================================
# 검토 실행 중 자동 수집
# ======================================================================
def ensure_ordinances(sido: str, sigungu: str) -> list:
    """
    해당 지자체의 풍력 이격거리 조례를 확보해 돌려준다.

    DB에 있으면 그대로, 없으면 자치법규 API로 1회 수집·저장한 뒤 돌려준다.
    수집에 실패하거나 규정이 없으면 하루 동안 재시도하지 않는다(외부 API 부담 방지).
    조회 실패를 예외로 올리지 않는다 — 호출부는 빈 목록을 UNKNOWN으로 다루면 된다.
    """
    from .models import LocalOrdinance

    def fetched() -> list:
        return list(LocalOrdinance.objects.filter(
            sigungu=sigungu, energy_type__in=['WIND', 'ALL']))

    if not sigungu:
        return []
    rows = fetched()
    if rows:
        return rows

    miss_key = f'windsite:ord_miss:{sido}:{sigungu}'
    try:
        if cache.get(miss_key):
            return []
    except Exception:                                           # noqa: BLE001
        pass

    with _lock_for(f'{sido}:{sigungu}'):
        rows = fetched()          # 락 대기 중 다른 스레드가 채웠을 수 있다
        if rows:
            return rows
        try:
            result = sync_sigungu(sido, sigungu, apply=True)
        except Exception:                                       # noqa: BLE001
            logger.exception('조례 자동 수집 실패 %s %s', sido, sigungu)
            result = None
        if not result or not result['entries']:
            try:
                cache.set(miss_key, True, _MISS_TTL)
            except Exception:                                   # noqa: BLE001
                pass
            return []
        return fetched()


def forget_miss(sido: str, sigungu: str) -> None:
    """자동 수집 실패 기록을 지운다 (커맨드로 수동 수집한 뒤 호출)."""
    try:
        cache.delete(f'windsite:ord_miss:{sido}:{sigungu}')
    except Exception:                                           # noqa: BLE001
        pass


# ======================================================================
# 수집 본체
# ======================================================================
def sync_sigungu(sido: str, sigungu: str, apply: bool = False) -> dict:
    """
    한 지자체의 조례를 조회해 풍력 이격거리 항목을 추출한다.

    returns::

        {'ordinance': {...}|None,   # 선택된 조례 (못 찾으면 None)
         'search_hits': [...],      # 진단용 검색 결과
         'source': '조문'|'별표'|'',
         'label': '제20조의2' 등,
         'article_title': str,
         'entries': [{'target','detail','distance_m','sentence'}, ...],
         'applied': int, 'flagged': int}
    """
    blank = {'ordinance': None, 'search_hits': [], 'source': '', 'label': '',
             'article_title': '', 'entries': [], 'applied': 0, 'flagged': 0}

    hits = lawapi.search_ordinance(sigungu, '계획 조례')
    cand = [h for h in hits
            if sigungu in h['org'] and any(k in h['name'] for k in ORDINANCE_KEYWORDS)]
    if not cand:
        cand = [h for h in hits if sigungu in h['org'] and '계획' in h['name']]
    if not cand:
        return {**blank, 'search_hits': hits}

    target = cand[0]
    body = lawapi.fetch_ordinance_articles(target['mst'])

    found = extract_from_articles(body['articles'])
    source = '조문'
    if not found:
        # 조문에 없으면 별표를 본다 — 청도군처럼 별표에만 규정된 사례가 있다
        found = extract_from_appendices(target['mst'])
        source = '별표'
    if not found:
        return {**blank, 'ordinance': target, 'search_hits': hits}

    art, entries = found
    label = _article_label(art['no']) if source == '조문' else art['no']
    out = {**blank, 'ordinance': target, 'search_hits': hits, 'source': source,
           'label': label, 'article_title': art.get('title', ''), 'entries': entries}

    if apply:
        out['applied'], out['flagged'] = persist(sido, sigungu, target, label,
                                                 art, entries)
    return out


# ----------------------------------------------------------------------
@transaction.atomic
def persist(sido: str, sigungu: str, target: dict, label: str,
            art: dict, entries: list[dict]) -> tuple[int, int]:
    """추출 결과를 반영한다. → (반영 건수, 미확인 표시 건수)"""
    from .models import LawArticle, LocalOrdinance

    detail_url = 'https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=' + target['mst']

    # 원문 보관 — 판정 근거를 원문으로 되돌릴 수 있게
    LawArticle.objects.update_or_create(
        law_name=target['name'], article_label=label,
        defaults=dict(
            source_type='ORDINANCE', org=target.get('org', ''),
            law_id=target.get('ordin_id', ''), mst=target['mst'],
            article_no=str(art.get('no', ''))[:20],
            article_title=(art.get('title') or '')[:300],
            article_text=(art.get('text') or '')[:20000],
            effective_date=target.get('effective_date', ''),
            promulgated_date=target.get('promulgated', ''),
            source_url=detail_url,
            via_demo_account=lawapi.is_demo_account(),
        ),
    )

    existing = list(LocalOrdinance.objects.filter(sigungu=sigungu,
                                                 energy_type__in=['WIND', 'ALL']))
    sido = (sido or next((r.sido for r in existing if r.sido), '')
            or (target.get('org', '').split() or [''])[0])

    for e in entries:
        LocalOrdinance.objects.update_or_create(
            sigungu=sigungu, energy_type='WIND',
            target=e['target'], target_detail=e['detail'],
            defaults=dict(
                sido=sido,
                distance_m=e['distance_m'],
                ordinance_name=target['name'],
                article=label,
                difficulty='HIGH',
                confidence='HIGH',            # 원문 대조 완료
                verified_at=date.today(),
                source_url=detail_url,
                note=f"원문 대조 (시행 {fmt_date(target.get('effective_date', ''))}): "
                     f"{e['sentence'][:250]}",
            ),
        )

    # 원문에서 확인되지 않은 기존 레코드는 지우지 않고 표시만 한다
    stale = list(LocalOrdinance.objects.filter(
        sigungu=sigungu, energy_type='WIND', confidence__in=['LOW', 'MEDIUM'],
    ).exclude(target_detail__in=[e['detail'] for e in entries]))
    for s in stale:
        s.note = ((s.note or '') +
                  f'\n⚠️ {date.today()} 원문 대조에서 확인되지 않은 항목입니다. '
                  '개정으로 삭제됐거나 다른 조례에 있을 수 있어 수기 확인이 필요합니다.')
        s.save(update_fields=['note'])

    return len(entries), len(stale)


# ======================================================================
# 추출
# ======================================================================
def extract_from_articles(articles: list[dict]) -> tuple[dict, list[dict]] | None:
    """풍력 이격 조항을 담은 조를 찾아 (조, 추출항목)을 돌려준다."""
    for art in articles:
        text = art.get('text') or ''
        if '풍력' not in text:
            continue
        # 풍력 문단만 잘라낸다 — 태양광 조항의 수치를 섞지 않기 위함
        block = _wind_block(text)
        if not block:
            continue
        entries = entries_from(block)
        if entries:
            return art, entries
    return None


def extract_from_appendices(mst: str) -> tuple[dict, list[dict]] | None:
    """별표(HWP 첨부)에서 풍력 이격 기준을 찾는다."""
    try:
        appendices = lawapi.fetch_ordinance_appendices(mst)
    except Exception:                                           # noqa: BLE001
        logger.warning('별표 목록 조회 실패 mst=%s', mst)
        return None

    for ap in appendices:
        text = ap.get('text') or ''
        if not text and ap.get('file_type', '').lower() in ('hwp', 'hwpx'):
            try:
                text = lawapi.fetch_appendix_text(ap['file_url'])
            except Exception:                                   # noqa: BLE001
                logger.warning('별표 %s 첨부 판독 실패', ap.get('no'))
                continue
        if '풍력' not in text:
            continue
        block = _wind_block_plain(text)
        if not block:
            continue
        entries = entries_from(block)
        if entries:
            no = str(ap.get('no', '')).strip()
            label = f'[별표 {int(no)}]' if no.isdigit() else f'[별표 {no}]'
            return ({'no': label, 'title': ap.get('title', ''), 'text': text}, entries)
    return None


def entries_from(block: str) -> list[dict]:
    """
    항목 단위로 쪼개 거리 수치를 뽑는다.

    괄호 안 단서를 본문과 분리해 다룬다. 영양군 제18조의2를 그대로 훑으면
    ``1,000미터(단, 군도는 500미터)`` 의 500m가 본문 문맥을 상세로 물고 들어가고,
    ``2,000미터(5호 미만 … 1,500미터)`` 의 1,500m는 본문과 상세가 같아져
    중복 제거에 통째로 삼켜졌다. 단서는 **별도 조건 항목**으로 남겨야 한다.
    """
    entries: list[dict] = []
    for line in re.split(r'(?=\(\d+\)|\n?\s*\d+\.\s|[가-힣]\.\s)', block):
        sentence = re.sub(r'\s+', ' ', line).strip()
        # 괄호를 공백으로 바꾸면 해남군 '정온시설(…)물로부터'가 '정온시설 물'로 잘린다.
        # 괄호 바깥 공백은 원문에 이미 있으므로 빈 문자열로 지운다.
        base = _PAREN.sub('', line)           # 괄호 밖 본문
        target = _target_of(line)
        parent_detail = ''

        for m in re.finditer(_DIST, base):
            dist = _to_meters(m.group(1), m.group(2))
            if dist is None:
                continue
            detail = _detail_of(base[:m.start()] or base)
            parent_detail = parent_detail or detail
            entries.append({'target': target, 'detail': detail,
                            'distance_m': dist, 'sentence': sentence})

        # 괄호 안 단서 — 본문 상세를 접두로 붙여 서로 구분되게 한다
        for p in _PAREN.finditer(line):
            inner = p.group(1)
            cond = _condition_of(inner)
            for m in re.finditer(_DIST, inner):
                dist = _to_meters(m.group(1), m.group(2))
                if dist is None:
                    continue
                entries.append({
                    'target': target,
                    'detail': f'{parent_detail[:40]}({cond})'.strip() if parent_detail else cond,
                    'distance_m': dist,
                    'sentence': sentence,
                })

    # 같은 (대상, 상세)가 중복되면 첫 값만 남긴다
    seen: set[tuple[str, str]] = set()
    out = []
    for e in entries:
        k = (e['target'], e['detail'])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _target_of(line: str) -> str:
    for code, pat in TARGET_PATTERNS:
        if re.search(pat, line):
            return code
    return 'OTHER'


# ----------------------------------------------------------------------
def _wind_block(text: str) -> str:
    """'풍력'이 등장하는 항(①②③…) 하나만 잘라낸다."""
    marks = '①②③④⑤⑥⑦⑧⑨⑩'
    positions = [(m.start(), m.group(0)) for m in re.finditer(f'[{marks}]', text)]
    if not positions:
        return text if '풍력' in text else ''
    for idx, (pos, _) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        block = text[pos:end]
        if '풍력' in block:
            return block
    return ''


def _wind_block_plain(text: str) -> str:
    """
    별표처럼 항 기호(①②③)가 없는 평문에서 풍력 관련 구간만 잘라낸다.
    '풍력발전시설은 …' 부터 다음 대항목(가./나./다.) 직전까지.
    태양광 기준의 수치를 섞으면 판정이 통째로 틀어지므로 범위를 좁게 잡는다.
    """
    m = re.search(r'[가-힣]\.\s*풍력발전시설', text) or re.search(r'풍력', text)
    if not m:
        return ''
    tail = text[m.start():]
    # 다음 대항목('바.' 같은 한글 항목 기호)에서 끊는다
    nxt = re.search(r'\s[가-힣]\.\s(?!풍력)', tail[10:])
    return tail[:10 + nxt.start()] if nxt else tail[:1500]


def _to_meters(num: str, unit: str) -> int | None:
    try:
        v = float(num.replace(',', ''))
    except ValueError:
        return None
    if unit.lower() in ('킬로미터', 'km'):
        v *= 1000
    return int(round(v)) if v > 0 else None


def _condition_of(inner: str) -> str:
    """'단, 군도는 500미터' → '군도는' / '5호 미만의 거주 가구가 있는 경우 1,500미터' → 그대로"""
    s = re.sub(_DIST, ' ', inner)
    s = re.sub(r'^[\s,]*(?:단|다만|이하)[\s,]*', '', s)
    s = re.sub(r'\s+', ' ', s).strip(' ,.·')
    return s[:60] or '단서'


def _detail_of(line: str) -> str:
    """'(2) 10호 이상 취락지역으로부터 2,000미터…' → '10호 이상 취락지역'"""
    # 항목 기호 제거: '(1)', '1.', '가.'
    line = re.sub(r'^\s*(?:\(\d+\)|\d+\.|[가-힣]\.)\s*', '', line.strip())
    m = re.search(r'(\d+호\s*(?:이상|미만)\s*[가-힣]+)', line)
    if m:
        return m.group(1).replace(' ', '')
    m = re.search(r'([가-힣·\s]{2,24}?)(?:으로부터|로부터|에서부터|와의|과의|경계)', line)
    if m:
        return re.sub(r'\s+', ' ', m.group(1)).strip()[:40]
    return re.sub(r'\s+', ' ', line).strip()[:40]


def _article_label(no: str) -> str:
    a, sub = lawapi.ordinance_article_key(no)
    return f'제{a}조의{sub}' if sub else f'제{a}조'


def fmt_date(yyyymmdd: str) -> str:
    s = (yyyymmdd or '').strip()
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 else (s or '-')
