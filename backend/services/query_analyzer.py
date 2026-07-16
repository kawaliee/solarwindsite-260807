"""
쿼리 분석기 — 사용자 질문에서 메타데이터 필터 조건을 자동 추출
예: "당진 EPC 계약서 지체상금" → project_name='당진', document_type='contract', sub_type='EPC'
"""
import re
import logging

logger = logging.getLogger(__name__)

# ── 프로젝트명 사전 ──
# key: 사용자가 입력할 수 있는 키워드 (소문자)
# value: DB에 실제 저장된 project_name 목록 (MatchAny에 그대로 전달됨)
PROJECT_KEYWORD_MAP = {
    '당진': ['당진 1단계', '당진 2단계'],
    '당진행복솔라': ['당진 1단계', '당진 2단계'],
    '대호솔라': ['당진 2단계'],
    '당진 1단계': ['당진 1단계'],
    '당진 2단계': ['당진 2단계'],
    '태평': ['태평'],
    '신안증도': ['태평'],
    '신안증도태양광': ['태평'],
    'taepyoeng': ['태평'],
    '홍성': ['홍성'],
    '홍성빛나래': ['홍성'],
    '홍성빛나래솔라': ['홍성'],
}

# ── 문서 대분류(document_type) 키워드 매핑 ──
DOC_TYPE_KEYWORDS = {
    'contract': ['계약서', '계약', '약정서', '약정', '합의서', '합의', 'contract', 'agreement'],
    'report': ['보고서', '보고자료', '보고', '투자심의', '투소위', 'report'],
    'financial': ['재무모델', '재무', 'FS', 'financial', '현금흐름', 'cashflow'],
    'permit': ['인허가', '허가', '허가증', '인가', 'permit', '발전사업허가'],
    'supervision': ['감리', '준공', '감리보고서', 'supervision'],
    'om': ['운영', '유지보수', 'O&M', 'OM', '월간보고서'],
}

# ── sub_type 키워드 매핑 ──
SUB_TYPE_KEYWORDS = {
    'EPC': ['EPC', 'epc', '도급', '시공'],
    'O&M': ['O&M', 'OM', 'O_M', '유지보수', '운영관리'],
    'PPA': ['PPA', 'ppa', '전력거래', '전력수급', '전력판매'],
    'PF': ['PF', 'pf', '프로젝트파이낸싱', '대출', '대출약정', 'loan', '금융'],
    'REC': ['REC', 'rec', '신재생에너지공급인증서'],
    'SPA': ['SPA', 'spa', '주식매매', '지분매각'],
    'SHA': ['SHA', 'sha', '주주간', '주주간계약'],
}

# ── 단계 키워드 ──
STAGE_PATTERNS = [
    (re.compile(r'1\s*단계|1단계|1st'), '1단계'),
    (re.compile(r'2\s*단계|2단계|2nd'), '2단계'),
]


def analyze_query(query: str) -> dict:
    """
    사용자 질문을 분석하여 메타데이터 필터 조건을 추출한다.

    Returns
    -------
    dict
        {
            'project_names': list[str],   # 매칭된 프로젝트명 목록 (Qdrant project_name 필드용)
            'document_type': str | None,  # 정규화된 문서 유형
            'sub_type': str | None,       # EPC, PPA 등
            'stage': str | None,          # 1단계, 2단계
            'clean_query': str,           # 필터 키워드가 제거된 순수 의미 쿼리
        }
    """
    result = {
        'project_names': [],
        'document_type': None,
        'sub_type': None,
        'stage': None,
        'clean_query': query,
    }

    query_lower = query.lower()

    # ── 1. 프로젝트명 추출 ──
    matched_db_names = set()
    for keyword, db_names in PROJECT_KEYWORD_MAP.items():
        if keyword.lower() in query_lower:
            matched_db_names.update(db_names)
    result['project_names'] = list(matched_db_names)

    # ── 2. 단계 추출 → 프로젝트명 좁히기 ──
    for pattern, stage_label in STAGE_PATTERNS:
        if pattern.search(query):
            result['stage'] = stage_label
            break

    # 단계가 특정되었으면 해당 단계만 남기기 (예: "당진 1단계" → ['당진 1단계']만)
    if result['stage'] and result['project_names']:
        narrowed = [n for n in result['project_names'] if result['stage'] in n]
        if narrowed:
            result['project_names'] = narrowed

    # ── 3. sub_type 추출 (EPC, PPA 등 — 더 구체적이므로 먼저) ──
    for sub_type, keywords in SUB_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in query_lower:
                result['sub_type'] = sub_type
                break
        if result['sub_type']:
            break

    # ── 4. document_type 추출 ──
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in query_lower:
                result['document_type'] = doc_type
                break
        if result['document_type']:
            break

    # sub_type이 EPC/PPA/PF 등이면 document_type을 contract로 보완
    if result['sub_type'] in ('EPC', 'O&M', 'PPA', 'PF', 'SPA', 'SHA') and not result['document_type']:
        result['document_type'] = 'contract'

    logger.info(f"쿼리 분석 결과: project={result['project_names']}, "
                f"doc_type={result['document_type']}, sub_type={result['sub_type']}, "
                f"stage={result['stage']}")

    return result


def refresh_project_aliases():
    """
    DB에서 현재 프로젝트 목록을 읽어 PROJECT_ALIASES를 동적으로 갱신한다.
    (서버 시작 시 또는 문서 업로드 후 호출)
    """
    global PROJECT_ALIASES
    try:
        from apps.documents.models import Document
        projects = Document.objects.values_list('project__name', flat=True).distinct()
        for name in projects:
            if not name:
                continue
            # 기본 키로 공백 앞 단어 사용 (예: "당진 1단계" → "당진")
            base = name.split()[0] if ' ' in name else name
            if base not in PROJECT_ALIASES:
                PROJECT_ALIASES[base] = [base, name]
            elif name not in PROJECT_ALIASES[base]:
                PROJECT_ALIASES[base].append(name)
        logger.info(f"프로젝트 사전 갱신 완료: {list(PROJECT_ALIASES.keys())}")
    except Exception as e:
        logger.warning(f"프로젝트 사전 갱신 실패: {e}")
