"""
폴더 경로 기반 메타데이터 추출기
[PJT명(SPC명)_단계]/[문서대분류]/[중간분류?]/[파일명] 구조를 파싱하여
project_name, spc_name, document_type, sub_type, source_file 등을 반환한다.
"""
import re
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 문서 대분류(document_type) 정규화 맵 ──────────────────────────
DOC_TYPE_MAP = {
    '계약서': 'contract',
    'contract': 'contract',
    '보고자료': 'report',
    '보고서': 'report',
    'report': 'report',
    '재무모델': 'financial',
    '재무': 'financial',
    'financial': 'financial',
    '인허가': 'permit',
    '감리': 'supervision',
    'o&m': 'om',
    'om': 'om',
    'epc': 'epc',
    'ppa': 'ppa',
    'pf': 'pf',
    '전력거래': 'power_trading',
    '법령': 'regulation',
    'spc': 'spc',
    '지분매각': 'equity_sale',
}

# ── 최상위 폴더에서 PJT명/SPC명 추출 패턴 ────────────────────────
# 예: "당진PJT(당진행복솔라)_1단계" → ("당진", "당진행복솔라", "1단계")
# 예: "태평 PJT(신안증도태양광)" → ("태평", "신안증도태양광", "")
PJT_PATTERN = re.compile(
    r'^(.+?)(?:PJT)?\s*[\(（]([^)）]+)[\)）](?:_(.+))?$',
    re.IGNORECASE
)


def extract_metadata_from_path(filepath: str, media_root: str) -> dict:
    """
    파일 경로로부터 메타데이터를 추출한다.

    Parameters
    ----------
    filepath : str
        파일의 절대 경로 (예: /app/media/당진PJT(당진행복솔라)_1단계/계약서/EPC/계약서.pdf)
    media_root : str
        media 폴더 루트 경로 (예: /app/media)

    Returns
    -------
    dict
        {
            'project_name': str,   # 예: "당진 1단계"
            'spc_name': str,       # 예: "당진행복솔라"
            'pjt_folder': str,     # 최상위 폴더 원본명
            'document_type': str,  # 예: "contract" (정규화)
            'document_type_raw': str,  # 예: "계약서" (원본)
            'sub_type': str,       # 예: "EPC"
            'source_file': str,    # 예: "계약서.pdf"
            'relative_path': str,  # media root 기준 상대 경로
        }
    """
    try:
        rel_path = os.path.relpath(filepath, media_root)
        parts = Path(rel_path).parts  # 경로를 폴더 단위로 분리

        result = {
            'project_name': '',
            'spc_name': '',
            'pjt_folder': '',
            'document_type': 'general',
            'document_type_raw': '',
            'sub_type': '',
            'source_file': os.path.basename(filepath),
            'relative_path': rel_path,
        }

        if not parts:
            return result

        # ── 1. 최상위 폴더: PJT명, SPC명 추출 ──────────────────
        top_folder = parts[0]
        result['pjt_folder'] = top_folder
        project_name, spc_name = _parse_pjt_folder(top_folder)
        result['project_name'] = project_name
        result['spc_name'] = spc_name

        # ── 2. 문서 대분류(document_type) 추출 ─────────────────
        # parts: [pjt_folder, doc_type, (sub_type?), ..., filename]
        if len(parts) >= 3:  # 최소: PJT / 대분류 / 파일명
            raw_doc_type = parts[1]
            result['document_type_raw'] = raw_doc_type
            result['document_type'] = _normalize_doc_type(raw_doc_type)

        # ── 3. 중간분류(sub_type) 추출 ─────────────────────────
        if len(parts) >= 4:
            raw_sub = parts[2]
            result['sub_type'] = _normalize_sub_type(raw_sub)

        # ── 4. 파일명에서 sub_type 보완 ─────────────────────────
        if not result['sub_type']:
            result['sub_type'] = _extract_sub_type_from_filename(result['source_file'])

        return result

    except Exception as e:
        logger.warning(f'메타데이터 추출 실패 ({filepath}): {e}')
        return {
            'project_name': '',
            'spc_name': '',
            'pjt_folder': '',
            'document_type': 'general',
            'document_type_raw': '',
            'sub_type': '',
            'source_file': os.path.basename(filepath),
            'relative_path': filepath,
        }


def _parse_pjt_folder(folder_name: str) -> tuple[str, str]:
    """
    최상위 폴더명에서 (project_name, spc_name)을 추출한다.

    예:
      "당진PJT(당진행복솔라)_1단계" → ("당진 1단계", "당진행복솔라")
      "태평 PJT(신안증도태양광)"    → ("태평", "신안증도태양광")
      "홍성PJT(홍성빛나래솔라)"     → ("홍성", "홍성빛나래솔라")
    """
    m = PJT_PATTERN.match(folder_name)
    if m:
        pjt_base = m.group(1).replace('PJT', '').replace('pjt', '').strip()
        spc_name = m.group(2).strip()
        stage = m.group(3).strip() if m.group(3) else ''
        project_name = f'{pjt_base} {stage}'.strip() if stage else pjt_base
        return project_name, spc_name

    # 패턴 매칭 실패 시 폴더명 그대로 사용
    clean = folder_name.replace('PJT', '').replace('pjt', '').strip()
    return clean, ''


def _normalize_doc_type(raw: str) -> str:
    """문서 대분류 폴더명을 표준 코드로 정규화"""
    key = raw.lower().strip()
    return DOC_TYPE_MAP.get(key, 'general')


def _normalize_sub_type(raw: str) -> str:
    """중간 분류 폴더명을 대문자 코드로 정규화"""
    upper = raw.upper().strip()
    # 번호 prefix 제거 (예: "01. 입찰" → "입찰")
    clean = re.sub(r'^\d+\.\s*', '', raw).strip()
    # 알려진 타입 우선
    lower = clean.lower()
    if lower in DOC_TYPE_MAP:
        return DOC_TYPE_MAP[lower].upper()
    return clean


def _extract_sub_type_from_filename(filename: str) -> str:
    """
    파일명에서 sub_type 키워드를 추출한다.
    예: "EPC계약서_v1.0.pdf" → "EPC"
        "OEM_Final.docx"    → "O&M"
        "PPA_2026.pdf"      → "PPA"
    """
    patterns = [
        (r'(?<![A-Za-z])EPC(?![A-Za-z])', 'EPC'),
        (r'(?<![A-Za-z])O[&_]?M(?![A-Za-z])', 'O&M'),
        (r'(?<![A-Za-z])PPA(?![A-Za-z])', 'PPA'),
        (r'(?<![A-Za-z])PF(?![A-Za-z])', 'PF'),
        (r'(?<![A-Za-z])SPA(?![A-Za-z])', 'SPA'),
        (r'(?<![A-Za-z])SHA(?![A-Za-z])', 'SHA'),
        (r'(?<![A-Za-z])REC(?![A-Za-z])', 'REC'),
        (r'(?<![A-Za-z])NDA(?![A-Za-z])', 'NDA'),
        (r'(?<![A-Za-z])MOU(?![A-Za-z])', 'MOU'),
    ]
    for pattern, label in patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            return label
    return ''


def get_chunking_strategy(document_type: str) -> str:
    """
    document_type에 따라 청킹 전략 코드를 반환한다.

    Returns
    -------
    str
        'contract'   - 조항 중심 Parent-Child 청킹
        'report'     - 마크다운 헤더 기반 청킹
        'financial'  - 표 보존 청킹
        'general'    - 기본 청킹
    """
    if document_type in ('contract', 'epc', 'ppa', 'om', 'pf', 'equity_sale'):
        return 'contract'
    if document_type in ('report', 'supervision', 'permit'):
        return 'report'
    if document_type in ('financial',):
        return 'financial'
    return 'general'
