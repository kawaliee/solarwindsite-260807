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

#: 이격거리가 **도시·군계획 조례가 아닌 별도 조례**에 있는 지자체가 있다.
#: 종전에는 '계획 조례'만 찾아, 별도 제정한 곳은 조례가 없는 것처럼 보였다.
#: 아래 검색어로 한 번 더 훑고, 이름이 걸리는 조례를 모두 후보로 둔다.
STANDALONE_QUERIES = ('풍력', '이격거리', '재생에너지 발전시설')
STANDALONE_KEYWORDS = ('풍력', '풍력발전', '재생에너지', '신재생에너지', '이격거리')

#: 거리 수치 표현. '2,000미터' / '2000m' / '1.5킬로미터'
_DIST = r'([0-9][0-9,\.]*)\s*(미터|m|M|킬로미터|km|KM)'

#: 단서 괄호 — '(단, 군도는 500미터)', '(5호 미만 … 1,500미터)'
_PAREN = re.compile(r'[(（]([^()（）]*)[)）]')

#: 이격거리가 **아닌** 수치의 문맥. 삼척시 별표 29에서 울타리 높이 2m와
#: 설비-울타리 간격 3m, '호 산정 방법'의 주택 간 50m가 이격거리로 잘못 잡혔다.
_NON_SEPARATION = ('높이', '울타리', '산정 방법', '산정방법', '주택 간', '주택간',
                   '건물 외벽', '차로', '폭')
#: 수치 앞 어디까지 훑어 위 문맥을 볼지
_CTX_WINDOW = 40

#: 표 한 행 — '<대상> N미터 이상 이격 M미터 이상 이격'
#: 태양광·풍력 2열 비교표에서 두 수치를 한 번에 잡아 열을 구분한다.
#: 라벨에 숫자가 들어가므로('주거밀집지역 (5호 이상)') 숫자를 배제하지 않는다.
#: 비탐욕 매칭이 뒤따르는 '<수치>미터 이상 이격'을 만족하는 지점까지 알아서 늘어난다.
_TABLE_ROW = re.compile(
    r'(?P<label>[^\n]{1,80}?)'
    r'(?P<d1>[0-9][0-9,\.]*)\s*(?:미터|m|M|킬로미터|km|KM)\s*이상\s*이격\s*'
    r'(?P<d2>[0-9][0-9,\.]*)\s*(?:미터|m|M|킬로미터|km|KM)\s*이상\s*이격'
)
#: 표 머리글 잔여물 — 첫 행 라벨 앞에 '태양에너지 설비 풍력에너지 설비 비고'가 붙는다
_TABLE_HEAD_JUNK = re.compile(r'^.*(?:설비|비고)\s*')
#: 표 아래 정의·비고 구간 시작 — 여기서 끊지 않으면 설명문의 수치가 섞인다
_TABLE_END = re.compile(r'\*\s*상기|[0-9]\.\s*[“"]|\(비고\)')
#: 별표 번호 — 여러 별표가 한 파일에 붙어 오는 경우 실제 번호를 찾는다
_APPENDIX_NO = re.compile(r'\[별표\s*(\d+)\s*(?:의\s*\d+)?\]')

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
        except lawapi.LawApiAuthError as e:
            # 조례가 없는 게 아니라 서버 IP 미등록 등으로 못 물어본 것이다.
            # 이걸 miss로 캐시하면 인증을 고친 뒤에도 하루 동안 계속 빈손이 된다.
            # 실패 사실은 남기되 재시도를 막지 않는다.
            logger.warning('조례 조회 인증 실패 %s %s: %s', sido, sigungu, e)
            _note_auth_failure(str(e))
            return []
        except Exception:                                       # noqa: BLE001
            logger.exception('조례 자동 수집 실패 %s %s', sido, sigungu)
            result = None
        if not result or not result['entries']:
            # 빈손인 이유를 남긴다. '조례를 확인했는데 이격 규정이 없다'와
            # '조례 자체를 못 찾았다'는 전혀 다른 사실이다. 앞은 그 지자체에
            # 조례상 이격 제한이 없다는 확정 정보이고, 뒤는 모른다는 뜻이다.
            reason = NO_RULE if (result and result.get('ordinance')) else NOT_FOUND
            try:
                cache.set(miss_key, reason, _MISS_TTL)
            except Exception:                                   # noqa: BLE001
                pass
            return []
        return fetched()


#: ensure_ordinances가 빈 목록을 돌려준 이유
NO_RULE = 'NO_RULE'        # 조례를 확인했고 풍력 이격 규정이 없다 (확정)
NOT_FOUND = 'NOT_FOUND'    # 해당 조례를 찾지 못했다 (미확인)
UNVERIFIED = 'UNVERIFIED'  # 인증 실패 등으로 물어보지 못했다 (미확인)
HAS_RULES = 'HAS_RULES'


def ordinance_state(sido: str, sigungu: str) -> tuple[list, str]:
    """
    (규정 목록, 상태) 를 돌려준다.

    ensure_ordinances()는 빈 목록만 주므로 '규정이 없다'와 '모른다'가
    구분되지 않는다. 면적을 집계할 때 이 둘을 같이 다루면, 이격 제한이
    없어 멀쩡히 쓸 수 있는 땅까지 판정 보류로 묶여 가용면적이 실제보다
    작게 나온다. (태백시 도시계획 조례 — 조문 104개·별표 25건 전부
    판독했으나 풍력 이격 조항이 실제로 없다)
    """
    rules = ensure_ordinances(sido, sigungu)
    if rules:
        return rules, HAS_RULES
    if auth_failure():
        return [], UNVERIFIED
    try:
        reason = cache.get(f'windsite:ord_miss:{sido}:{sigungu}')
    except Exception:                                           # noqa: BLE001
        reason = None
    if reason == NO_RULE:
        return [], NO_RULE
    return [], NOT_FOUND


#: 최근 인증 실패 메시지. 조례가 비었을 때 '없음'인지 '못 물어봄'인지
#: 화면·보고서에서 구분해 쓰기 위한 것이다.
_AUTH_FAILURE_KEY = 'windsite:law_auth_fail'
_AUTH_FAILURE_TTL = 60 * 30


def _note_auth_failure(message: str) -> None:
    try:
        cache.set(_AUTH_FAILURE_KEY, message, _AUTH_FAILURE_TTL)
    except Exception:                                           # noqa: BLE001
        pass


def auth_failure() -> str:
    """최근 30분 내 law.go.kr 인증 실패 메시지. 없으면 빈 문자열."""
    try:
        return cache.get(_AUTH_FAILURE_KEY) or ''
    except Exception:                                           # noqa: BLE001
        return ''


def _as_date(yyyymmdd: str):
    """'20250808' → date. 형식이 다르면 None (추측하지 않는다)."""
    t = (yyyymmdd or '').strip()
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return date(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None


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

    # 별도 제정 조례도 후보에 넣는다. 계획 조례를 찾았더라도 함께 본다 —
    # 계획 조례에 이격 규정이 없고 별도 조례에만 있는 지자체가 있다.
    seen = {h['mst'] for h in cand}
    for kw in STANDALONE_QUERIES:
        try:
            extra = lawapi.search_ordinance(sigungu, kw)
        except lawapi.LawApiAuthError:
            raise
        except Exception:                                       # noqa: BLE001
            logger.warning('별도 조례 검색 실패 %s %s', sigungu, kw)
            continue
        hits += [h for h in extra if h['mst'] not in {x['mst'] for x in hits}]
        for h in extra:
            if (h['mst'] not in seen and sigungu in h['org']
                    and any(k in h['name'] for k in STANDALONE_KEYWORDS)):
                seen.add(h['mst'])
                cand.append(h)

    if not cand:
        return {**blank, 'search_hits': hits}

    # 첫 조례에서 못 찾으면 다음 후보로 넘어간다. 종전에는 cand[0]만 보고
    # 끝내, 계획 조례에 규정이 없으면 '이격 규정 없음'으로 확정해 버렸다.
    target = cand[0]
    found, source = None, ''
    body: dict = {}
    for h in cand:
        body = lawapi.fetch_ordinance_articles(h['mst'])
        got = extract_from_articles(body['articles'])
        src = '조문'
        if not got:
            # 조문에 없으면 별표를 본다 — 청도군처럼 별표에만 규정된 사례가 있다
            got = extract_from_appendices(h['mst'])
            src = '별표'
        if got:
            target, found, source = h, got, src
            break

    if not found:
        return {**blank, 'ordinance': target, 'search_hits': hits}

    art, entries = found
    label = _article_label(art['no']) if source == '조문' else art['no']
    out = {**blank, 'ordinance': target, 'search_hits': hits, 'source': source,
           'label': label, 'article_title': art.get('title', ''), 'entries': entries}

    if apply:
        out['applied'], out['flagged'] = persist(sido, sigungu, target, label,
                                                 art, entries,
                                                 addenda=body.get('addenda'))
    return out


# ----------------------------------------------------------------------
@transaction.atomic
def persist(sido: str, sigungu: str, target: dict, label: str,
            art: dict, entries: list[dict], addenda: list[dict] | None = None
            ) -> tuple[int, int]:
    """추출 결과를 반영한다. → (반영 건수, 미확인 표시 건수)"""
    from .models import LawArticle, LocalOrdinance

    detail_url = 'https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=' + target['mst']
    # 시행일과 부칙을 함께 보관한다. 조례 개정 전에 발전사업허가를 받은 사업은
    # 부칙 경과조치로 종전 기준이 적용될 수 있어 이격 판정이 통째로 뒤집힌다.
    # 시행일을 모르면 그 검토 자체가 촉발되지 않는다.
    eff = _as_date(target.get('effective_date', ''))
    addenda_text = lawapi.relevant_addenda(addenda or [])
    pt = lawapi.power_transition(addenda or [])
    gf_date = _as_date((pt or {}).get('date', ''))
    gf_basis = (pt or {}).get('date_basis', '')

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
                effective_date=eff,
                grandfather_date=gf_date,
                grandfather_basis=gf_basis,
                addenda=addenda_text,
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

        # ① 태양광·풍력 2열 비교표를 먼저 본다. 표가 있으면 그쪽이 정답이다.
        table = table_entries_from(text)
        if table:
            entries, pos = table
            return ({'no': _appendix_label(text, pos, ap.get('no')),
                     'title': ap.get('title', ''), 'text': text}, entries)

        # ② 표가 없으면 항목 기호로 풍력 구간을 잘라낸다 (청도군 '마. 풍력발전시설…')
        block = _wind_block_plain(text)
        if not block:
            continue
        entries = entries_from(block)
        if entries:
            pos = text.find(block[:30]) if block else -1
            return ({'no': _appendix_label(text, pos, ap.get('no')),
                     'title': ap.get('title', ''), 'text': text}, entries)
    return None


def _appendix_label(text: str, pos: int, fallback_no) -> str:
    """
    실제 별표 번호를 찾는다.

    자치법규 API는 '별표 1부터 별표 29'를 한 파일로 주는 경우가 있다.
    그때 ap['no']는 '0001'이라 근거 표기가 틀린다(삼척시는 별표 29가 맞다).
    본문에서 해당 위치 **앞쪽의 가장 가까운 [별표 N]** 을 근거로 삼는다.
    """
    if pos and pos > 0:
        marks = list(_APPENDIX_NO.finditer(text, 0, pos))
        if marks:
            return f'[별표 {marks[-1].group(1)}]'
    no = str(fallback_no or '').strip()
    return f'[별표 {int(no)}]' if no.isdigit() else f'[별표 {no}]'


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
            if dist is None or _is_non_separation(base, m.start()):
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
                if dist is None or _is_non_separation(inner, m.start()):
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


def _is_non_separation(text: str, pos: int) -> bool:
    """수치 바로 앞 문맥이 이격거리가 아닌 규정이면 True."""
    ctx = text[max(0, pos - _CTX_WINDOW):pos]
    return any(k in ctx for k in _NON_SEPARATION)


# ----------------------------------------------------------------------
def table_entries_from(text: str) -> tuple[list[dict], int] | None:
    """
    태양광·풍력 **2열 비교표**에서 풍력 열만 뽑는다. → (항목들, 표 시작위치)

    삼척시 별표 29가 이 형태다. 표가 평문으로 흘러들어오면 한 줄에 두 수치가
    나란히 놓이는데, 종전 파서는 앞에 오는 **태양광 수치를 집어갔다.**
    그 결과 주거밀집 이격이 2,000m인데 500m로 저장됐다 — 판정을 뒤집는 오류다.

        구 분 태양에너지 설비 풍력에너지 설비 비고
        도로 500미터 이상 이격 1,000미터 이상 이격
        주거밀집지역 (5호 이상) 500미터 이상 이격 2,000미터 이상 이격

    열 순서는 머리글에서 '태양'과 '풍력'의 등장 순서로 판별한다. 둘 중 하나라도
    없으면 표로 보지 않는다 — 추측해서 열을 고르지 않는다.
    """
    for head_m in re.finditer(r'구\s*분', text):
        head = text[head_m.end(): head_m.end() + 80]
        i_sun, i_wind = head.find('태양'), head.find('풍력')
        if i_sun < 0 or i_wind < 0:
            continue
        wind_second = i_wind > i_sun

        body = text[head_m.end():]
        end = _TABLE_END.search(body)
        if end:
            body = body[:end.start()]

        entries: list[dict] = []
        for row in _TABLE_ROW.finditer(body):
            label = re.sub(r'\s+', ' ', row.group('label')).strip(' ·,')
            # 머리글 잔여물('태양에너지 설비 풍력에너지 설비 비고')을 떨어낸다.
            # 탐욕 매칭이라 마지막 '설비/비고'까지 잘라 대상명만 남는다.
            label = _TABLE_HEAD_JUNK.sub('', label).strip(' ·,')
            if not label:
                continue
            dist = _to_meters(row.group('d2') if wind_second else row.group('d1'), '미터')
            if dist is None:
                continue
            entries.append({
                'target': _target_of(label),
                'detail': label[:60],
                'distance_m': dist,
                'sentence': re.sub(r'\s+', ' ', row.group(0)).strip(),
            })

        if entries:
            return entries, head_m.start()
    return None


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
    # '마. 풍력발전시설은 …' 같은 항목 시작을 우선 잡는다.
    # 맨 앞의 '풍력'을 그냥 집으면 삼척시처럼 제목('태양에너지 및 풍력에너지 설비의
    # 설치에 대한 허가 기준')에 걸려 표와 정의문을 통째로 쓸어담는다.
    m = (re.search(r'[가-힣]\.\s*풍력\s*(?:발전시설|에너지)', text)
         or re.search(r'풍력\s*(?:발전시설|에너지\s*설비)\s*(?:은|는)', text)
         or re.search(r'풍력', text))
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
