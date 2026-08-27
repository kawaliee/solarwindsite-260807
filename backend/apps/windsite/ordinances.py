"""
지자체 이격거리 조례 수집·대조 서비스
---------------------------------------------------------------
자치법규 OPEN API로 조례 원문을 받아 **에너지원별** 이격거리 조항을 추출한다.

풍력과 태양광은 같은 조례의 같은 조·같은 별표에 나란히 실린다. 그래서 수집
경로는 하나로 두고, 원문에서 어느 구간을 잘라낼지만 에너지원 프로파일
(`energy.py`)로 가른다. 삼척시 별표 29처럼 2열 비교표인 경우 열을 잘못
고르면 판정이 통째로 뒤집히므로, 잘라내는 규칙을 프로파일 한 곳에 모았다.

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

from . import energy as energy_mod, lawapi

logger = logging.getLogger(__name__)

#: 조례명 후보 — 지자체마다 제명이 다르다 (도시계획 / 군계획 / 도시·군계획)
ORDINANCE_KEYWORDS = ('도시계획 조례', '군계획 조례', '도시·군계획 조례', '도시군계획 조례')

#: 이격거리가 **도시·군계획 조례가 아닌 별도 조례**에 있는 지자체가 있다.
#: 종전에는 '계획 조례'만 찾아, 별도 제정한 곳은 조례가 없는 것처럼 보였다.
#: 별도 조례 검색어와 제명 낱말은 에너지원마다 다르므로 energy.py가 들고 있다
#: (검색어는 **낱말 하나씩**. 자치법규 검색은 토큰을 모두 만족하는 조례만
#: 주므로 '재생에너지 발전시설'처럼 붙이면 0건이 된다 — 실측).

#: 거리 수치 표현. '2,000미터' / '2000m' / '1.5킬로미터' / **'1천미터'**
#:
#: ⚠️ 한글 수사('천'·'만')를 받지 않으면 조문 하나가 통째로 사라진다.
#:    장흥군 제20조의2 제1호 "고속도로와 국도에서는 **1천미터**, 지방도와
#:    군도에서는 500미터"에서 1,000m 구간이 잡히지 않아, 국도 이격이 없는
#:    것으로 판정됐다. 조례 원문은 아라비아 숫자와 한글 수사를 섞어 쓴다.
_DIST = r'([0-9][0-9,\.]*)\s*(천|만)?\s*(미터|m|M|킬로미터|km|KM)'

#: 단서 괄호 — '(단, 군도는 500미터)', '(5호 미만 … 1,500미터)'
_PAREN = re.compile(r'[(（]([^()（）]*)[)）]')

#: '반경 500미터 안에 10호 이상의 거주 유무' — **호수를 세는 반경**이지
#: 이격거리가 아니다. 이 둘을 섞으면 거리는 맞아도 군집 판정이 틀어진다.
_COUNT_RADIUS = re.compile(
    r'반\s*경[^)）]{0,20}?[0-9][0-9,\.]*\s*(?:천|만)?\s*(?:미터|m|M)'
    r'[^)）]{0,20}?\d+\s*호\s*이상')
#: 이 표시가 붙은 항목은 이격이 아니라 산정 기준이다. `available._rule_set`이
#: 보고 군집 반경으로 쓴다.
COUNT_RADIUS_TAG = '호수산정반경'

#: 이격거리가 **아닌** 수치의 문맥. 삼척시 별표 29에서 울타리 높이 2m와
#: 설비-울타리 간격 3m, '호 산정 방법'의 주택 간 50m가 이격거리로 잘못 잡혔다.
_NON_SEPARATION = ('높이', '울타리', '산정 방법', '산정방법', '주택 간', '주택간',
                   '건물 외벽', '차로', '폭',
                   # 설비를 어떻게 놓을지에 대한 규정 — 이격 대상과의 거리가 아니다.
                   # 홍성군 [별표 26] '가로방향으로 연속하여 설치할 경우 100미터
                   # 이내로 설치'가 주거 이격 100m로 잘못 잡혔다.
                   '가로방향', '연속하여', '이격공간', '설치할 경우')
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
def _miss_key(code: str, sido: str, sigungu: str) -> str:
    # 에너지원을 키에 넣는다. 풍력 조례가 없다고 태양광 조회까지 하루 동안
    # 막히면, 같은 조례 안에 태양광 기준만 있는 지자체를 통째로 놓친다.
    return f'windsite:ord_miss:{code}:{sido}:{sigungu}'


def ensure_ordinances(sido: str, sigungu: str,
                      energy: str = energy_mod.DEFAULT) -> list:
    """
    해당 지자체의 **해당 에너지원** 이격거리 조례를 확보해 돌려준다.

    DB에 있으면 그대로, 없으면 자치법규 API로 1회 수집·저장한 뒤 돌려준다.
    수집에 실패하거나 규정이 없으면 하루 동안 재시도하지 않는다(외부 API 부담 방지).
    조회 실패를 예외로 올리지 않는다 — 호출부는 빈 목록을 UNKNOWN으로 다루면 된다.
    """
    from .models import LocalOrdinance

    prof = energy_mod.profile(energy)

    def fetched() -> list:
        return list(LocalOrdinance.objects.filter(
            sigungu=sigungu, energy_type__in=[prof.code, 'ALL']))

    if not sigungu:
        return []
    rows = fetched()
    if rows:
        return rows

    miss_key = _miss_key(prof.code, sido, sigungu)
    try:
        if cache.get(miss_key):
            return []
    except Exception:                                           # noqa: BLE001
        pass

    with _lock_for(f'{prof.code}:{sido}:{sigungu}'):
        rows = fetched()          # 락 대기 중 다른 스레드가 채웠을 수 있다
        if rows:
            return rows
        try:
            result = sync_sigungu(sido, sigungu, apply=True, energy=prof.code)
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


def ordinance_state(sido: str, sigungu: str,
                    energy: str = energy_mod.DEFAULT) -> tuple[list, str]:
    """
    (규정 목록, 상태) 를 돌려준다.

    ensure_ordinances()는 빈 목록만 주므로 '규정이 없다'와 '모른다'가
    구분되지 않는다. 면적을 집계할 때 이 둘을 같이 다루면, 이격 제한이
    없어 멀쩡히 쓸 수 있는 땅까지 판정 보류로 묶여 가용면적이 실제보다
    작게 나온다. (태백시 도시계획 조례 — 조문 104개·별표 25건 전부
    판독했으나 풍력 이격 조항이 실제로 없다)
    """
    prof = energy_mod.profile(energy)
    rules = ensure_ordinances(sido, sigungu, prof.code)
    if rules:
        return rules, HAS_RULES
    if auth_failure():
        return [], UNVERIFIED
    try:
        reason = cache.get(_miss_key(prof.code, sido, sigungu))
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


def forget_miss(sido: str, sigungu: str, energy: str | None = None) -> None:
    """
    자동 수집 실패 기록을 지운다 (커맨드로 수동 수집한 뒤 호출).

    energy를 주지 않으면 에너지원 전부를 지운다 — 조례 원문을 새로 등록했다면
    같은 조례에 실린 다른 에너지원 기준도 다시 볼 값이기 때문이다.
    """
    codes = [energy_mod.profile(energy).code] if energy else list(energy_mod.PROFILES)
    for code in codes:
        try:
            cache.delete(_miss_key(code, sido, sigungu))
        except Exception:                                       # noqa: BLE001
            pass


# ======================================================================
# 수집 본체
# ======================================================================
def sync_sigungu(sido: str, sigungu: str, apply: bool = False,
                 energy: str = energy_mod.DEFAULT) -> dict:
    """
    한 지자체의 조례를 조회해 **해당 에너지원의** 이격거리 항목을 추출한다.

    returns::

        {'ordinance': {...}|None,   # 선택된 조례 (못 찾으면 None)
         'search_hits': [...],      # 진단용 검색 결과
         'source': '조문'|'별표'|'',
         'label': '제20조의2' 등,
         'article_title': str,
         'entries': [{'target','detail','distance_m','sentence'}, ...],
         'applied': int, 'flagged': int}
    """
    prof = energy_mod.profile(energy)
    blank = {'ordinance': None, 'search_hits': [], 'source': '', 'label': '',
             'article_title': '', 'entries': [], 'applied': 0, 'flagged': 0,
             'energy_type': prof.code}

    hits = lawapi.search_ordinance(sigungu, '계획 조례')
    cand = [h for h in hits
            if sigungu in h['org'] and any(k in h['name'] for k in ORDINANCE_KEYWORDS)]
    if not cand:
        cand = [h for h in hits if sigungu in h['org'] and '계획' in h['name']]

    # 별도 제정 조례도 후보에 넣는다. 계획 조례를 찾았더라도 함께 본다 —
    # 계획 조례에 이격 규정이 없고 별도 조례에만 있는 지자체가 있다.
    seen = {h['mst'] for h in cand}
    for kw in prof.search_queries:
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
                    and any(k in h['name'] for k in prof.search_keywords)):
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
        got = extract_from_articles(body['articles'], prof)
        src = '조문'
        if not got:
            # 조문에 없으면 별표를 본다 — 청도군처럼 별표에만 규정된 사례가 있다
            got = extract_from_appendices(h['mst'], prof)
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
                                                 addenda=body.get('addenda'),
                                                 energy=prof.code)
    return out


# ----------------------------------------------------------------------
@transaction.atomic
def persist(sido: str, sigungu: str, target: dict, label: str,
            art: dict, entries: list[dict], addenda: list[dict] | None = None,
            energy: str = energy_mod.DEFAULT) -> tuple[int, int]:
    """추출 결과를 반영한다. → (반영 건수, 미확인 표시 건수)"""
    from .models import LawArticle, LocalOrdinance

    prof = energy_mod.profile(energy)
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

    existing = list(LocalOrdinance.objects.filter(
        sigungu=sigungu, energy_type__in=[prof.code, 'ALL']))
    sido = (sido or next((r.sido for r in existing if r.sido), '')
            or (target.get('org', '').split() or [''])[0])

    for e in entries:
        LocalOrdinance.objects.update_or_create(
            sigungu=sigungu, energy_type=prof.code,
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
        sigungu=sigungu, energy_type=prof.code, confidence__in=['LOW', 'MEDIUM'],
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
#: **용어의 정의** 조는 이격 조항으로 채택하지 않는다.
#:
#: 정의 조에는 거리 수치가 잔뜩 들어 있지만 이격거리가 아니다. 해남군
#: 제18조의2가 그 예다 — "주택호수의 산정 기준은 … 직선거리 100미터이내의
#: 가구수", "몽리민이란 … 반경 500미터이내". 조 번호가 앞서 있어 먼저 걸리는데,
#: 그대로 집으면 도로 이격 100m·주거 이격 500m로 저장돼 **판정이 통째로
#: 틀어진다**(실제 해남군 태양광 기준은 제19조의3의 도로 100m·주거 500m로
#: 우연히 비슷해 더 알아채기 어렵다).
#:
#: 풍력에서 드러나지 않았던 이유는 그 정의 조에 '풍력'이 없어 애초에 걸리지
#: 않았기 때문이다. 태양광으로 넓히면서 드러난 함정이다.
_DEFINITION_TITLE = ('정의',)


def _is_definition_article(art: dict) -> bool:
    return any(k in (art.get('title') or '') for k in _DEFINITION_TITLE)


def extract_from_articles(articles: list[dict], prof) -> tuple[dict, list[dict]] | None:
    """해당 에너지원의 이격 조항을 담은 조를 찾아 (조, 추출항목)을 돌려준다."""
    for art in articles:
        text = art.get('text') or ''
        if not prof.mentioned_in(text):
            continue
        if _is_definition_article(art):
            continue
        # 해당 에너지원 문단만 잘라낸다 — 다른 에너지원 수치를 섞지 않기 위함
        block = _energy_block(text, prof)
        if not block:
            continue
        entries = entries_from(block)
        if entries:
            return art, entries
    return None


def extract_from_appendices(mst: str, prof) -> tuple[dict, list[dict]] | None:
    """별표(HWP 첨부)에서 해당 에너지원의 이격 기준을 찾는다."""
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
        if not prof.mentioned_in(text):
            continue

        # ① 태양광·풍력 2열 비교표를 먼저 본다. 표가 있으면 그쪽이 정답이다.
        table = table_entries_from(text, prof)
        if table:
            entries, pos = table
            return ({'no': _appendix_label(text, pos, ap.get('no')),
                     'title': ap.get('title', ''), 'text': text}, entries)

        # ② 표가 없으면 항목 기호로 해당 구간을 잘라낸다 (청도군 '마. 풍력발전시설…')
        block = _energy_block_plain(text, prof)
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
    return _entries_from(_denoise(block))


def _entries_from(block: str) -> list[dict]:
    """
    항목 단위로 쪼개 거리 수치를 뽑는다.

    괄호 안 단서를 본문과 분리해 다룬다. 영양군 제18조의2를 그대로 훑으면
    ``1,000미터(단, 군도는 500미터)`` 의 500m가 본문 문맥을 상세로 물고 들어가고,
    ``2,000미터(5호 미만 … 1,500미터)`` 의 1,500m는 본문과 상세가 같아져
    중복 제거에 통째로 삼켜졌다. 단서는 **별도 조건 항목**으로 남겨야 한다.
    """
    entries: list[dict] = []
    # 항목 기호는 조례마다 다르다 — '(1)' '1.' '1)' '가.' 를 모두 끊는다.
    # '1)' 를 빠뜨리면 홍성군 [별표 26]처럼 항목이 한 덩어리로 뭉쳐,
    # 도로 50m·주거 300m가 서로의 상세를 물고 들어가 구분되지 않는다.
    for line in re.split(r'(?=\(\d+\)|\n?\s*\d+[\.\)]\s|[가-힣]\.\s)', block):
        sentence = re.sub(r'\s+', ' ', line).strip()
        # 괄호를 공백으로 바꾸면 해남군 '정온시설(…)물로부터'가 '정온시설 물'로 잘린다.
        # 괄호 바깥 공백은 원문에 이미 있으므로 빈 문자열로 지운다.
        base = _PAREN.sub('', line)           # 괄호 밖 본문
        target = _target_of(line)
        parent_detail = ''

        for m in re.finditer(_DIST, base):
            dist = _to_meters(m.group(1), m.group(3), m.group(2))
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
                dist = _to_meters(m.group(1), m.group(3), m.group(2))
                if dist is None or _is_non_separation(inner, m.start()):
                    continue
                # '반경 500미터 안에 10호 이상' 은 **이격거리가 아니라 호수를
                # 세는 반경**이다. 이격으로 오해하면 거리는 우연히 맞아도
                # 군집 판정이 틀어져 결과가 통째로 달라진다(장흥 실측:
                # 주거 이격 9.1ha → 0.2ha). 따로 표시해 둔다.
                if _COUNT_RADIUS.search(inner):
                    entries.append({
                        'target': target, 'detail': f'{COUNT_RADIUS_TAG}(호 기준)',
                        'distance_m': dist, 'sentence': sentence,
                    })
                    continue
                # 괄호 안에 호수 구간이 있으면 **그것이 이 수치의 조건**이다.
                # 장흥군 제20조의2 제2호의 괄호는 "반경 500미터 안에 10호
                # 이상의 거주 유무" — 본문 앞구절('10호 미만')을 붙이면
                # 이상·미만이 뒤바뀌어 300m와 500m가 서로 자리를 바꾼다.
                inner_detail = _detail_of(inner) if re.search(r'\d+호\s*(?:이상|미만)', inner) else ''
                entries.append({
                    'target': target,
                    'detail': inner_detail or (
                        f'{parent_detail[:40]}({cond})'.strip() if parent_detail else cond),
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
def table_entries_from(text: str, prof) -> tuple[list[dict], int] | None:
    """
    태양광·풍력 **2열 비교표**에서 해당 에너지원 열만 뽑는다. → (항목들, 표 시작위치)

    삼척시 별표 29가 이 형태다. 표가 평문으로 흘러들어오면 한 줄에 두 수치가
    나란히 놓이는데, 종전 파서는 앞에 오는 **태양광 수치를 집어갔다.**
    그 결과 주거밀집 이격이 2,000m인데 500m로 저장됐다 — 판정을 뒤집는 오류다.

        구 분 태양에너지 설비 풍력에너지 설비 비고
        도로 500미터 이상 이격 1,000미터 이상 이격
        주거밀집지역 (5호 이상) 500미터 이상 이격 2,000미터 이상 이격

    열 순서는 머리글에서 '태양'과 '풍력'의 등장 순서로 판별한다. 둘 중 하나라도
    없으면 표로 보지 않는다 — 추측해서 열을 고르지 않는다. 이 안전장치는
    에너지원이 늘어도 그대로 둔다. 열이 하나뿐인 표에서 짝을 맞춰 읽으면
    남의 에너지원 수치를 그대로 집어가기 때문이다.
    """
    for head_m in re.finditer(r'구\s*분', text):
        head = text[head_m.end(): head_m.end() + 80]
        i_sun, i_wind = head.find('태양'), head.find('풍력')
        if i_sun < 0 or i_wind < 0:
            continue
        # 내가 읽어야 할 열이 뒤쪽인가
        mine = i_sun if prof.table_marker == '태양' else i_wind
        other = i_wind if prof.table_marker == '태양' else i_sun
        mine_second = mine > other

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
            dist = _to_meters(row.group('d2') if mine_second else row.group('d1'), '미터')
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
def _energy_block(text: str, prof) -> str:
    """해당 에너지원이 등장하는 항(①②③…) 하나만 잘라낸다."""
    marks = '①②③④⑤⑥⑦⑧⑨⑩'
    positions = [(m.start(), m.group(0)) for m in re.finditer(f'[{marks}]', text)]
    if not positions:
        return text if prof.mentioned_in(text) else ''
    for idx, (pos, _) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        block = text[pos:end]
        if prof.mentioned_in(block):
            return block
    return ''


#: 항목 기호 없이 시작하는 별표에서 훑을 최대 길이(자).
#: 홍성군 [별표 26]이 4,107자다. 별표 하나를 다 담을 만큼은 되어야 한다.
_MAX_PLAIN_BLOCK = 5000

#: 별표 하나에 **여러 시설의 허가기준**이 실리는 경우가 있다.
#: 홍성군 [별표 26] '태양광발전시설 **등 특정시설**에 대한 허가기준'이 그렇다 —
#: 태양광 다음에 묘지·장례식장 기준이 이어진다. 구간을 끊지 않으면 장례식장
#: 주거 이격 200m가 태양광 기준으로 저장된다.
_FACILITY_HEADING = re.compile(r'\d+\.\s*[가-힣ㆍ·,()\s]{2,40}?허가기준')

#: HWP 이진 레코드가 문단 텍스트에 섞여 들어온 잡음(예: '†普', '湯慴', '汤捯').
#: 국내 조례 본문은 사실상 한글이라, 한자 덩어리는 판독 잔여물로 본다.
#: 값(숫자+미터)에는 영향이 없고 **대상 이름**을 가려 라벨이 비게 만든다 —
#: 홍성군에서 '자연취락지구 및 주거밀집지역 †普 으로부터'의 라벨이 빈 값이 됐다.
_HWP_NOISE = re.compile(r'[\u3400-\u9FFF\u2E80-\u2FDF†]+')


def _denoise(text: str) -> str:
    return re.sub(r'\s{2,}', ' ', _HWP_NOISE.sub(' ', text))


def _other_markers(prof) -> list[str]:
    """다른 에너지원을 가리키는 낱말 — 그 구간이 시작되면 거기서 끊는다."""
    return [p.table_marker for c, p in energy_mod.PROFILES.items()
            if c != prof.code]


def _energy_block_plain(text: str, prof) -> str:
    """
    별표처럼 항 기호(①②③)가 없는 평문에서 해당 에너지원 구간만 잘라낸다.

    두 가지 생김새를 구분해 다룬다. 하나로 다루면 한쪽이 반드시 깨진다.

    **①  항목 기호로 시작하는 별표** — '마. 풍력발전시설은 …'
        그 항목부터 다음 대항목(바.) 직전까지가 그 에너지원의 구간이다.

    **②  제목만 있고 본문이 곧바로 항목으로 들어가는 별표**
        홍성군 [별표 26] '태양광발전시설 등 특정시설에 대한 허가기준'이 그렇다.
        제목 뒤 본문이 '가. 발전시설 입지는 …'으로 시작하는데, ①의 규칙을
        그대로 쓰면 **그 첫 '가.'에서 끊겨** 수치가 하나도 남지 않는다
        (도로 50m·주거밀집 300m·7호 미만 100m가 전부 사라졌다).
        그래서 대항목이 아니라 **다른 에너지원 구간이 시작되는 지점**에서
        끊는다. 다른 에너지원이 없으면 별표 끝까지 본다.

    어느 쪽이든 다른 에너지원의 수치를 섞지 않는 것이 목적이다.
    """
    # ① 항목 기호로 시작하는 경우
    m = re.search(prof.block_patterns[0], text)
    if m:
        tail = text[m.start():]
        nxt = re.search(r'\s[가-힣]\.\s(?!' + prof.block_stop_negative + r')',
                        tail[10:])
        return tail[:10 + nxt.start()] if nxt else tail[:1500]

    # ② 제목만 걸리는 경우 — 다른 에너지원이 나오는 곳에서 끊는다
    m = next((mm for mm in (re.search(pat, text)
                            for pat in prof.block_patterns[1:]) if mm), None)
    if not m:
        return ''
    tail = text[m.start():]
    cuts = [c.start() for c in
            (re.search(k, tail[10:]) for k in _other_markers(prof)) if c]
    # 같은 별표 안의 **다른 시설** 허가기준에서도 끊는다. 우리 에너지원을
    # 가리키지 않는 첫 머리글이 경계다.
    for h in _FACILITY_HEADING.finditer(tail, 10):
        if not prof.mentioned_in(h.group(0)):
            cuts.append(h.start() - 10)
            break
    return tail[:10 + min(cuts)] if cuts else tail[:_MAX_PLAIN_BLOCK]


def _to_meters(num: str, unit: str, scale: str = '') -> int | None:
    """'1' + 천 + '미터' → 1000. 한글 수사는 배수로 곱한다."""
    try:
        v = float(num.replace(',', ''))
    except ValueError:
        return None
    v *= {'천': 1000, '만': 10000}.get((scale or '').strip(), 1)
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
    """
    '(2) 10호 이상 취락지역으로부터 2,000미터…' → '10호 이상 취락지역'

    ⚠️ 인자로는 **수치 바로 앞까지**의 글을 받는다. 한 호에 구간이 둘 있으면
       (장흥군 제20조의2 제2호 "10호 이상 … 500미터 … 10호 미만 … 300미터")
       거리마다 **가장 가까운 앞쪽 구절**을 상세로 삼아야 한다. 앞에서부터
       찾으면 300미터에 '10호이상의'가 붙어 이상·미만이 뒤바뀐다.
    """
    # 항목 기호 제거: '(1)', '1.', '가.'
    line = re.sub(r'^\s*(?:\(\d+\)|\d+\.|[가-힣]\.)\s*', '', line.strip())
    # 호수 구간은 **마지막 것**을 쓴다 — 수치 바로 앞의 조건이 그 수치의 것이다.
    ms = list(re.finditer(r'(\d+호\s*(?:이상|미만)\s*(?:의\s*)?[가-힣]+)', line))
    if ms:
        return ms[-1].group(1).replace(' ', '')
    # 도로처럼 대상이 여럿 나열된 호도 마지막 구절이 그 수치의 것이다.
    #   '고속도로와 국도에서는 1천미터, 지방도와 군도에서는 500미터'
    ms = list(re.finditer(r'([가-힣]{2,}(?:와|과)\s*[가-힣]{2,})\s*에서는', line))
    if ms:
        return ms[-1].group(1).replace(' ', '')
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


# ======================================================================
# 거리가 아닌 조건 — 경사도
# ======================================================================
#: '경사도가 15도 이상인 산지에 입지하지 아니할 것' / '평균경사도 25도 이하'
#: 숫자와 '도' 사이에 조사가 끼기도 해 사이를 느슨하게 둔다.
_SLOPE = re.compile(r'(평균\s*)?경사도[^\n]{0,40}?([0-9]{1,2})\s*도')

#: 수치 주변에서 에너지원을 확인할 때 훑는 범위(자)
_SLOPE_CTX = 150


def slope_limit(sido: str, sigungu: str,
                energy: str = energy_mod.DEFAULT) -> dict | None:
    """
    조례가 정한 **경사도 상한**을 돌려준다. 없으면 None.

        {'degrees': 15, 'law': '홍성군 군계획 조례',
         'article': '[별표 26]', 'sentence': '…'}

    이격거리 조례에는 거리가 아닌 지형 조건이 섞여 있다. 거리 추출기가 잡지
    못하는 종류라 따로 읽는다. 새로 조회하지 않고 **이미 보관한 조문 원문**
    (LawArticle)에서 찾는다 — 이격거리를 수집할 때 함께 저장해 둔 그 원문이다.

    ⚠️ 같은 조·별표에 태양광과 풍력이 함께 실리므로, 수치 주변에 우리
       에너지원이 언급되는지 확인하고 채택한다. 확인되지 않으면 **쓰지 않는다** —
       남의 에너지원 기준을 이 사업의 상한으로 적용하는 것보다 모른다고 하는
       편이 낫다.
    """
    from .models import LawArticle

    prof = energy_mod.profile(energy)
    others = [p.table_marker for c, p in energy_mod.PROFILES.items() if c != prof.code]

    rows = LawArticle.objects.filter(source_type='ORDINANCE', org__icontains=sigungu)
    for art in rows:
        text = _denoise(art.article_text or '')
        if not prof.mentioned_in(text):
            continue
        for m in _SLOPE.finditer(text):
            ctx = text[max(0, m.start() - _SLOPE_CTX): m.end() + _SLOPE_CTX]
            # 수치 근처에 다른 에너지원만 있으면 그쪽 기준이다 — 넘어간다.
            near_mine = prof.mentioned_in(ctx)
            near_other = any(k in ctx for k in others)
            if near_other and not near_mine:
                continue
            # 근처에 아무 에너지원도 없으면, 조문 전체가 우리 것일 때만 받는다.
            if not near_mine and any(k in text for k in others):
                continue
            try:
                deg = int(m.group(2))
            except ValueError:
                continue
            if not 5 <= deg <= 45:          # 조례 경사도 기준의 현실적 범위
                continue
            return {
                'degrees': deg,
                'law': art.law_name,
                'article': art.article_label,
                'source_url': art.source_url,
                'sentence': _clause_around(text, m),
            }
    return None


#: 항목 기호 — 근거 문장을 앞 항목까지 물고 들어가지 않도록 여기서 끊는다.
_ITEM_MARK = re.compile(r'(?:\(\d+\)|\d+[\.\)]|[가-힣]\.)\s')


def _clause_around(text: str, m: re.Match) -> str:
    """
    수치가 놓인 **항목 하나**만 잘라 근거 문장으로 쓴다.

    앞뒤로 고정 길이를 잘라내면 옆 항목이 딸려 온다. 실제로 홍성군 경사도
    근거에 '3) 농업생산기반이 정비되어 있고…'가 섞여 나왔다. 보고서에 그대로
    인용되므로, 근거로 제시하는 문장은 그 조항만이어야 한다.
    """
    starts = [x.start() for x in _ITEM_MARK.finditer(text, 0, m.start())]
    lo = starts[-1] if starts else max(0, m.start() - 60)
    nxt = _ITEM_MARK.search(text, m.end())
    hi = nxt.start() if nxt else min(len(text), m.end() + 80)
    return re.sub(r'\s+', ' ', text[lo:hi]).strip(' ,.·')
