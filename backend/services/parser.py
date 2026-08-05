import re
import logging
import struct
from typing import List

logger = logging.getLogger(__name__)

# HWP Parser Hang Bug 픽스를 위한 몽키 패치
try:
    from hwp_hwpx_parser.hwp5 import HWP5Reader
    
    def _patched_extract_hyperlink_texts_from_para(self, para_data: bytes) -> List[str]:
        hyperlink_texts = []
        i = 0
        CTRL_ID_HYPERLINK = 0x6B6E6C68  # 'hlnk'

        while i < len(para_data) - 1:
            code = struct.unpack_from("<H", para_data, i)[0]

            if code == 0x03:
                if i + 6 <= len(para_data):
                    ctrl_id = struct.unpack_from("<I", para_data, i + 2)[0]

                    if ctrl_id == CTRL_ID_HYPERLINK:
                        text_start = i + 14
                        text_chars = []
                        j = text_start

                        while j < len(para_data) - 1:
                            c = struct.unpack_from("<H", para_data, j)[0]
                            if c == 0x04:
                                break
                            elif c == 0x03:
                                j += 2
                            elif 0x20 <= c < 0x10000:
                                text_chars.append(chr(c))
                                j += 2
                            else:
                                j += 2

                        if text_chars:
                            hyperlink_texts.append("".join(text_chars))
                        
                        i = j + 2
                        continue
            i += 2

        return hyperlink_texts

    HWP5Reader._extract_hyperlink_texts_from_para = _patched_extract_hyperlink_texts_from_para
    logger.info("Monkey patched HWP5Reader._extract_hyperlink_texts_from_para successfully to prevent Hang.")
except Exception as e:
    logger.warning(f"Failed to monkey patch HWP5Reader: {e}")

# --- chunk size ---
CHUNK_SIZE_CONTRACT = 400
CHUNK_OVERLAP_CONTRACT = 80
CHUNK_SIZE_GENERAL = 400
CHUNK_OVERLAP_GENERAL = 100
MAX_ARTICLE_SIZE = 800

# article boundary pattern
ARTICLE_RE = re.compile(r'(?:^|\n)\s*(제\s*\d+\s*조|Article\s*\d+)', re.MULTILINE | re.IGNORECASE)
# paragraph boundary
PARAGRAPH_RE = re.compile(r'(?:^|\n)\s*(?:[①-⑯]|\d+\.\s)', re.MULTILINE)
# annex pattern
ANNEX_RE = re.compile(r'(?:^|\n)\s*(별지|별표|특약|부속서|첨부)', re.MULTILINE)

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("HWP Parsing timed out")

def parse_hwp(filepath, strategy='general'):
    import signal
    try:
        # 60초 타임아웃 설정
        try:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(60)
        except AttributeError:
            pass # Windows 환경 등 SIGALRM 미지원 환경 대비

        from hwp_hwpx_parser import read
        reader = read(filepath)
        text = reader.text or ""
        
        table_mds = []
        try:
            table_mds = reader.get_tables_as_markdown()
        except Exception as te:
            logger.warning(f"HWP table extraction failed: {te}")
            
        reader.close()
        
        normalized_text = text
        if table_mds:
            normalized_text += '\n\n[표 데이터]\n' + '\n\n'.join(table_mds)
            
        doc_meta = {
            'original_format': filepath.split('.')[-1].lower(),
            'extraction_confidence': 1.0,
            'is_low_quality': False
        }
        
        total_len = len(normalized_text.strip())
        if total_len < 100:
            return {'page_count': 1, 'chunks': [], 'status': 'review_pending', 'doc_meta': doc_meta}
            
        if strategy == 'contract':
            chunks = chunk_normalized_contract(normalized_text, doc_meta)
            return {'page_count': max(1, len(normalized_text) // 1000), 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}
        else:
            chunks = _semantic_chunk(normalized_text, None, strategy)
            return {'page_count': max(1, len(normalized_text) // 1000), 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}
            
    except TimeoutException as te:
        logger.error(f'HWP parsing timed out: {filepath}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}
    except Exception as e:
        logger.error(f'HWP parsing failed: {e}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass


def parse_document(filepath, file_type, strategy='general'):
    file_type = file_type.lower().strip('.')
    if file_type == 'pdf':
        return parse_pdf(filepath, strategy)
    elif file_type in ('docx', 'doc'):
        return parse_docx(filepath, strategy)
    elif file_type in ('xlsx', 'xls', 'xlsm'):
        return parse_xlsx(filepath)
    elif file_type in ('pptx', 'ppt'):
        return parse_pptx(filepath)
    elif file_type in ('hwp', 'hwpx'):
        return parse_hwp(filepath, strategy)
    elif file_type in ('md', 'markdown'):
        return parse_markdown(filepath)
    else:
        logger.warning(f'Unsupported file type: {file_type}')
        return {'page_count': 0, 'chunks': []}


def parse_markdown(filepath):
    """
    마크다운 파싱 — 사업 Fact-sheet([사업개요] *.md) 적재 경로.

    헤더(##) 단위로 끊는다. Fact-sheet 는 섹션 제목마다 사업명을 반복해 두므로
    청크가 쪼개져도 "어느 사업 수치인지"가 조각 안에 남는다.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(filepath, 'r', encoding='cp949', errors='ignore') as f:
            text = f.read()
    except OSError as e:
        logger.error(f'Markdown read failed: {e}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}

    doc_meta = {
        'original_format': 'md',
        'extraction_confidence': 1.0,
        'is_low_quality': False,
    }

    if len(text.strip()) < 50:
        return {'page_count': 1, 'chunks': [], 'status': 'review_pending', 'doc_meta': doc_meta}

    chunks = _chunk_by_markdown_headers(text, None)
    return {
        'page_count': max(1, len(text) // 1500),
        'chunks': chunks,
        'status': 'indexed',
        'doc_meta': doc_meta,
    }


def parse_pdf(filepath, strategy='general'):
    import signal
    try:
        # PDF 파싱 타임아웃 240초(4분) 설정
        try:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(240)
        except AttributeError:
            pass

        from services.ocr_service import is_scanned_pdf, ocr_pdf
        import fitz
        import pdfplumber

        page_count = 0
        doc_meta = {
            'original_format': 'pdf',
            'extraction_confidence': 1.0,
            'is_low_quality': False
        }

        # 1. 스캔 PDF 판별
        scanned = is_scanned_pdf(filepath)
        pages_text = []

        if scanned:
            # 스캔 PDF -> OCR 구동
            ocr_res = ocr_pdf(filepath)
            pages_text = [(p['page_number'], p['text']) for p in ocr_res.get('pages', [])]
            doc_meta['extraction_confidence'] = round(ocr_res.get('average_confidence', 0.0) / 100.0, 2)
            if ocr_res.get('is_low_quality'):
                doc_meta['is_low_quality'] = True
            page_count = len(pages_text)
        else:
            # 텍스트 PDF -> fitz(PyMuPDF) 구조 정렬로 추출
            doc = fitz.open(filepath)
            page_count = len(doc)

            for i, page in enumerate(doc):
                blocks = page.get_text("blocks")
                # 2단 레이아웃 뒤섞임 보정을 위해 블록을 y-좌표(15px 오차 단위) 후 x-좌표로 정렬
                sorted_blocks = sorted(blocks, key=lambda b: (round(b[1] / 15) * 15, b[0]))
                page_text = "\n".join(b[4].strip() for b in sorted_blocks if b[4].strip())
                pages_text.append((i + 1, page_text))
            doc.close()

            # 테이블 데이터 마크다운 보강 (pdfplumber)
            try:
                with pdfplumber.open(filepath) as pdf:
                    for i, page in enumerate(pdf.pages):
                        tables = page.extract_tables()
                        table_mds = []
                        for table in tables:
                            md = []
                            for r_idx, row in enumerate(table):
                                clean_row = [str(c).replace('\n', ' ').strip() if c else '' for c in row]
                                md.append('| ' + ' | '.join(clean_row) + ' |')
                                if r_idx == 0:
                                    md.append('|' + '|'.join(['---'] * len(clean_row)) + '|')
                            if md:
                                table_mds.append('\n'.join(md))
                        if table_mds and i < len(pages_text):
                            pages_text[i] = (pages_text[i][0], pages_text[i][1] + '\n\n[표 데이터]\n' + '\n\n'.join(table_mds))
            except Exception as te:
                logger.warning(f"Table extraction failed: {te}")

        # 2. 후처리: 머리말·꼬리말·페이지번호 제거
        cleaned_pages = clean_pdf_headers_footers(pages_text)

        # 3. 후처리: 잘린 조항 이어붙이기 (Stitching)
        normalized_text = stitch_split_clauses(cleaned_pages)

        # 4. 파싱 상태 체크 (글자수 부족 시 수동 검토 분류)
        total_len = len(normalized_text.strip())
        if total_len < 100:
            return {'page_count': page_count, 'chunks': [], 'status': 'review_pending', 'doc_meta': doc_meta}

        if strategy == 'contract':
            chunks = chunk_normalized_contract(normalized_text, doc_meta)
            return {'page_count': page_count, 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}
        else:
            page_map = _build_page_map(cleaned_pages)
            chunks = _semantic_chunk(normalized_text, page_map, strategy)
            return {'page_count': page_count, 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}

    except TimeoutException as te:
        logger.error(f'PDF parsing timed out (OCR Hang detected): {filepath}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}
    except Exception as e:
        logger.error(f'PDF parsing failed: {e}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}
    finally:
        try:
            signal.alarm(0)
        except Exception:
            pass


def clean_pdf_headers_footers(pages_text: list) -> list:
    """페이지 상단 헤더, 하단 푸터 반복 문자열 및 페이지 번호 제거"""
    cleaned_pages = []
    page_lines_list = []
    for page_num, text in pages_text:
        lines = text.split('\n')
        page_lines_list.append((page_num, lines))

    # 앞/뒤 반복되는 헤더/푸터 라인 감지
    header_candidates = {}
    footer_candidates = {}
    total_pages = len(page_lines_list)

    if total_pages >= 3:
        for page_num, lines in page_lines_list:
            if len(lines) >= 1:
                k = lines[0].strip()
                if k: header_candidates[k] = header_candidates.get(k, 0) + 1
            if len(lines) >= 2:
                k = lines[1].strip()
                if k: header_candidates[k] = header_candidates.get(k, 0) + 1
            if len(lines) >= 1:
                k = lines[-1].strip()
                if k: footer_candidates[k] = footer_candidates.get(k, 0) + 1
            if len(lines) >= 2:
                k = lines[-2].strip()
                if k: footer_candidates[k] = footer_candidates.get(k, 0) + 1

        headers_to_remove = {k for k, v in header_candidates.items() if v >= total_pages * 0.5 and len(k) > 3}
        footers_to_remove = {k for k, v in footer_candidates.items() if v >= total_pages * 0.5 and len(k) > 3}
    else:
        headers_to_remove = set()
        footers_to_remove = set()

    page_num_pattern = re.compile(r'^\s*[-~]?\s*\d+\s*[-~]?\s*$|^\s*Page\s*\d+|^\s*\d+\s*/\s*\d+\s*$')

    for page_num, lines in page_lines_list:
        cleaned_lines = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped in headers_to_remove and idx < 3:
                continue
            if stripped in footers_to_remove and idx >= len(lines) - 3:
                continue
            if page_num_pattern.match(stripped):
                continue
            cleaned_lines.append(line)
        cleaned_pages.append((page_num, '\n'.join(cleaned_lines)))

    return cleaned_pages


def stitch_split_clauses(pages_text: list) -> str:
    """페이지 경계에서 미완성으로 잘린 조항 단락 이어붙이기"""
    if not pages_text:
        return ""

    full_text_parts = []
    sentence_end_pattern = re.compile(r'[.!?\"\'”]$')
    clause_start_pattern = re.compile(r'^\s*(제\s*\d+\s*조|Article\s*\d+)', re.IGNORECASE)

    for i in range(len(pages_text)):
        page_num, current_text = pages_text[i]
        current_text = current_text.strip()
        if not current_text:
            continue

        if i == 0:
            full_text_parts.append(current_text)
            continue

        prev_text = full_text_parts[-1].strip()

        # 이전 페이지 마지막 라인이 마침표로 끝나지 않고, 
        # 현재 페이지 첫 줄이 새 조항 시작이 아니면 한 문장으로 간주하고 이어붙임
        if prev_text and not sentence_end_pattern.search(prev_text) and not clause_start_pattern.match(current_text):
            full_text_parts[-1] = prev_text + " " + current_text
        else:
            full_text_parts.append(current_text)

    return '\n\n'.join(full_text_parts)


def parse_docx(filepath, strategy='general'):
    try:
        from docx import Document
        from pathlib import Path
        doc = Document(Path(filepath))
        lines = []
        doc_meta = {
            'original_format': 'docx',
            'extraction_confidence': 1.0,
            'is_low_quality': False
        }

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            
            # 문단 스타일(Heading/List) 보존
            if para.style and para.style.name:
                if 'Heading' in para.style.name:
                    lines.append(f"\n# {text}\n")
                    continue
                elif 'List' in para.style.name:
                    lines.append(f"- {text}")
                    continue
            lines.append(text)

        # 표 데이터 마크다운 추출 병합 (지능형 메타 요약 가드 탑재)
        table_mds = []
        for table in doc.tables:
            md = []
            cells_text = []
            for r_idx, row in enumerate(table.rows):
                clean_row = [str(cell.text).replace('\n', ' ').strip() for cell in row.cells]
                cells_text.extend(clean_row)
                md.append('| ' + ' | '.join(clean_row) + ' |')
                if r_idx == 0:
                    md.append('|' + '|'.join(['---'] * len(clean_row)) + '|')
            
            if md:
                md_content = '\n'.join(md)
                # 표 내용은 실제 문서에서 파싱한 마크다운만 그대로 적재한다.
                # (특정 대주단/지분/금액 등 기밀 수치를 소스에 하드코딩 주입하지 않는다 — 준수규칙 3.1 및 보안 정책)
                table_mds.append(md_content)

        normalized_text = '\n'.join(lines)
        if table_mds:
            normalized_text += '\n\n[표 데이터]\n' + '\n\n'.join(table_mds)

        total_len = len(normalized_text.strip())
        if total_len < 100:
            return {'page_count': 1, 'chunks': [], 'status': 'review_pending', 'doc_meta': doc_meta}

        if strategy == 'contract':
            chunks = chunk_normalized_contract(normalized_text, doc_meta)
            return {'page_count': max(1, len(doc.paragraphs) // 30), 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}
        else:
            chunks = _semantic_chunk(normalized_text, None, strategy)
            return {'page_count': max(1, len(doc.paragraphs) // 30), 'chunks': chunks, 'status': 'indexed', 'doc_meta': doc_meta}

    except Exception as e:
        logger.error(f'DOCX parsing failed: {e}')
        return {'page_count': 0, 'chunks': [], 'status': 'failed'}


def classify_clause_metadata(text: str, doc_meta: dict) -> dict:
    """규칙 및 키워드 기반 조항 메타데이터 태깅"""
    hangul_chars = len(re.findall(r'[ㄱ-ㅣ가-힣]', text))
    total_chars = len(text) if text else 1
    language = 'ko' if (hangul_chars / total_chars) > 0.05 else 'en'

    governing_law = '대한민국'
    if language == 'en':
        governing_law = '뉴욕주 (추정)'
    if any(k in text for k in ['대한민국', 'Korean Law', 'laws of Korea', 'laws of the Republic of Korea']):
        governing_law = '대한민국'
    elif any(k in text for k in ['England', '영국법', 'laws of England']):
        governing_law = '영국'
    elif any(k in text for k in ['New York', '뉴욕주법', 'laws of New York']):
        governing_law = '뉴욕주'

    article_type = '기타'
    type_keywords = {
        '비밀유지': ['비밀', '기밀', 'confidential', 'disclosure', 'secrecy'],
        '손해배상': ['손해배상', '배상', 'indemnification', 'indemnity', 'liability', 'damages'],
        '준거법': ['준거법', 'governing law', 'applicable law'],
        '분쟁해결': ['분쟁해결', '중재', '소송', '관할', 'dispute resolution', 'arbitration', 'jurisdiction'],
        'IP귀속': ['지식재산', '특허', '저작권', 'intellectual property', 'patent', 'copyright', '성과물'],
        '용역대금': ['대금', '지급', '금액', 'payment', 'fee', 'invoice', '단가'],
        '검수': ['검수', '인수', '검사', 'acceptance', 'inspection'],
        '계약기간/갱신': ['기간', '갱신', '기간 만료', '유효기간', 'term', 'renewal', 'expiration'],
        '해제/해지': ['해제', '해지', 'termination', 'terminate'],
        '불가항력': ['불가항력', 'force majeure'],
        '권리의무양도': ['양도', 'transfer', 'assignment', 'assign'],
        '하도급제한': ['하도급', '재위탁', 'subcontract'],
        '하자보수': ['하자보수', '하자 담보', '하자보증', 'warranty', 'defect'],
        '이사지명권': ['이사 지명', '이사 선임', 'board nominee', 'appoint director'],
        '우선매수권': ['우선매수', '우선 매수', 'ROFR', 'right of first refusal'],
        '동반매도권': ['동반매도', '동반 매도', 'tag-along', 'tag along'],
        '강제매도권': ['강제매도', '강제 매도', 'drag-along', 'drag along'],
        '교착상태': ['교착', 'deadlock'],
        '진술보장': ['진술 및 보장', '진술보장', 'representations and warranties'],
        '경업금지': ['경업금지', '경업 금지', 'non-compete', 'non compete'],
        '선행조건': ['선행조건', '조건 선행', 'CP', 'conditions precedent'],
    }

    matched_counts = {}
    lower_text = text.lower()
    for atype, kw_list in type_keywords.items():
        count = sum(1 for kw in kw_list if kw in lower_text)
        if count > 0:
            matched_counts[atype] = count

    if matched_counts:
        article_type = max(matched_counts, key=matched_counts.get)

    bias_tag = 'neutral'
    pro_buyer_words = ['갑의 승인', '발주자의 서면', '매수인의 동의', 'sole discretion of Buyer', 'written consent of Buyer']
    pro_seller_words = ['을은 책임을 지지', '수급인의 면책', 'seller shall not be liable', 'limitation of liability of Seller']

    buyer_score = sum(1 for w in pro_buyer_words if w in text)
    seller_score = sum(1 for w in pro_seller_words if w in text)

    if buyer_score > seller_score:
        bias_tag = 'pro-buyer'
    elif seller_score > buyer_score:
        bias_tag = 'pro-seller'

    industry = '일반'
    project_name = doc_meta.get('project_name', '').lower()
    if any(k in project_name for k in ['태양광', 'solar', '염해', '증도']):
        industry = '태양광'
    elif any(k in project_name for k in ['풍력', 'wind']):
        industry = '풍력'

    party_roles = '당사자'
    if article_type == 'EPC':
        party_roles = '발주자 / 수급인'
    elif article_type == 'SPA':
        party_roles = '매도인 / 매수인'
    elif article_type == 'LEASE':
        party_roles = '임대인 / 임차인'

    return {
        'article_type': article_type,
        'governing_law': governing_law,
        'language': language,
        'bias_tag': bias_tag,
        'industry': industry,
        'party_roles': party_roles,
        'original_format': doc_meta.get('original_format', 'pdf'),
        'extraction_confidence': doc_meta.get('extraction_confidence', 1.0)
    }


def chunk_normalized_contract(normalized_text, doc_meta):
    """정규화 텍스트 -> 조항 단위 분할 -> 메타데이터 태깅"""
    chunks = []
    
    # [표 데이터] 영역을 별개의 독립된 영역으로 분리하기 위해 boundary에 강제 삽입
    table_index = normalized_text.find('\n\n[표 데이터]\n')
    
    boundaries = [m.start() for m in ARTICLE_RE.finditer(normalized_text)]
    
    # 표 데이터 시작 인덱스가 존재하면 경계선에 추가
    if table_index != -1:
        # table_index보다 앞에 있는 조항 경계선만 유지
        boundaries = [b for b in boundaries if b < table_index]
        boundaries.append(table_index)
        
    if not boundaries:
        return _chunk_general(normalized_text, None)

    # 서두 정보 처리
    header = normalized_text[:boundaries[0]].strip()
    if header:
        # 서두(Header)가 너무 큰 경우 (예: 1500자 초과), 청크 비대화를 막기 위해 1500자 단위로 슬라이싱 분할
        MAX_HEADER_SIZE = 1500
        OVERLAP_SIZE = 200
        if len(header) > MAX_HEADER_SIZE:
            start_idx = 0
            sub_idx = 0
            while start_idx < len(header):
                end_idx = min(start_idx + MAX_HEADER_SIZE, len(header))
                seg_header = header[start_idx:end_idx].strip()
                if seg_header:
                    chunks.append({
                        'content': seg_header,
                        'page_number': 1,
                        'section_title': f'계약서 서두 (파트 {sub_idx+1})',
                        'article_number': None,
                        'article_title': '계약 정보',
                        'chunk_type': 'header',
                        'char_start': start_idx,
                        'char_end': end_idx,
                        'chunk_role': 'standalone',
                        'metadata': classify_clause_metadata(seg_header, doc_meta)
                    })
                start_idx += (MAX_HEADER_SIZE - OVERLAP_SIZE)
                sub_idx += 1
        else:
            chunks.append({
                'content': header,
                'page_number': 1,
                'section_title': '계약서 서두',
                'article_number': None,
                'article_title': '계약 정보',
                'chunk_type': 'header',
                'char_start': 0,
                'char_end': boundaries[0],
                'chunk_role': 'standalone',
                'metadata': classify_clause_metadata(header, doc_meta)
            })

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(normalized_text)
        seg = normalized_text[start:end].strip()
        if not seg:
            continue

        # 만약 이 단락이 [표 데이터] 로 시작하는 표 파트라면 독립 처리
        if seg.startswith('[표 데이터]'):
            parts = re.split(r'\n\s*\n', seg)
            offset = start
            for part in parts:
                part_clean = part.strip()
                if not part_clean or part_clean == '[표 데이터]':
                    continue
                
                # 표 제목으로 복원하기 위해 '[표 데이터]' 접두사가 없으면 붙여줌
                if not part_clean.startswith('[표 데이터]'):
                    part_to_chunk = '[표 데이터]\n' + part_clean
                else:
                    part_to_chunk = part_clean
                
                chunks.append({
                    'content': part_to_chunk,
                    'page_number': None,
                    'section_title': '별첨 표 데이터',
                    'article_number': None,
                    'article_title': '별첨 데이터',
                    'chunk_type': 'table',
                    'char_start': offset,
                    'char_end': offset + len(part_to_chunk),
                    'chunk_role': 'standalone',
                    'metadata': classify_clause_metadata(part_to_chunk, doc_meta)
                })
                offset += len(part_to_chunk) + 2
            continue

        art_num, art_title = _extract_article_info(seg)
        ctype = 'annex' if ANNEX_RE.search(seg) else 'article'

        # Parent 청크 생성
        clause_meta = classify_clause_metadata(seg, doc_meta)
        parent_chunk = {
            'content': seg,
            'page_number': None,
            'section_title': f'제{art_num}조' if art_num else art_title,
            'article_number': art_num,
            'article_title': art_title,
            'chunk_type': ctype,
            'char_start': start,
            'char_end': end,
            'chunk_role': 'parent',
            'metadata': clause_meta
        }
        chunks.append(parent_chunk)

        # Child 청크 분할 (RAG 검색용)
        child_chunks = _split_long_article_into_children(seg, start, ctype, art_num, art_title, None)
        for child in child_chunks:
            child['parent_ref_index'] = len(chunks) - 1
            child['metadata'] = clause_meta
        chunks.extend(child_chunks)

    return chunks


def _extract_article_info(text):
    m = re.match(r'\s*(?:제\s*(\d+)\s*조|Article\s*(\d+))\s*[(\[（【]?([^)\]）】\n]{0,40})[)\]）】]?', text, re.IGNORECASE)
    if m:
        art_num = int(m.group(1)) if m.group(1) else (int(m.group(2)) if m.group(2) else None)
        title = m.group(3).strip() if m.group(3) else ''
        return art_num, title
    return None, ''


def parse_xlsx(filepath):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        chunks = []
        offset = 0
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows_text = []
            for row in ws.iter_rows(values_only=True):
                row_str = ' | '.join(str(c) if c is not None else '' for c in row)
                if row_str.strip(' |'):
                    rows_text.append(row_str)
            if not rows_text:
                continue
            sheet_text = '\n'.join(rows_text)
            for chunk_text in _split_text(sheet_text, 400, 100):
                chunks.append({
                    'content': chunk_text,
                    'page_number': None,
                    'section_title': sheet_name,
                    'article_number': None,
                    'article_title': sheet_name,
                    'chunk_type': 'table',
                    'char_start': offset,
                    'char_end': offset + len(chunk_text),
                    'sheet_name': sheet_name,
                })
                offset += len(chunk_text)
        wb.close()
        return {'page_count': len(wb.sheetnames), 'chunks': chunks}
    except Exception as e:
        logger.error(f'XLSX parsing failed: {e}')
        return {'page_count': 0, 'chunks': []}


def parse_pptx(filepath):
    try:
        from pptx import Presentation
        from pathlib import Path
        prs = Presentation(Path(filepath))
        chunks = []
        offset = 0
        for slide_num, slide in enumerate(prs.slides):
            texts = [s.text.strip() for s in slide.shapes if hasattr(s, 'text') and s.text.strip()]
            text = '\n'.join(texts)
            if not text.strip():
                continue
            for chunk_text in _split_text(text, CHUNK_SIZE_GENERAL, CHUNK_OVERLAP_GENERAL):
                chunks.append({
                    'content': chunk_text,
                    'page_number': slide_num + 1,
                    'section_title': f'Slide {slide_num + 1}',
                    'article_number': None,
                    'article_title': f'슬라이드 {slide_num + 1}',
                    'chunk_type': 'general',
                    'char_start': offset,
                    'char_end': offset + len(chunk_text),
                })
                offset += len(chunk_text)
        return {'page_count': len(prs.slides), 'chunks': chunks}
    except Exception as e:
        logger.error(f'PPTX parsing failed: {e}')
        return {'page_count': 0, 'chunks': []}


# ---- semantic chunking ----

def _semantic_chunk(full_text, page_map, strategy='general'):
    if not full_text.strip():
        return []
        
    if strategy == 'contract':
        if ARTICLE_RE.search(full_text):
            return _chunk_by_articles(full_text, page_map)
        return _chunk_general(full_text, page_map)
    elif strategy == 'report':
        return _chunk_by_markdown_headers(full_text, page_map)
    elif strategy == 'financial':
        return _chunk_general(full_text, page_map) # financial은 이미 parse_xlsx에서 처리됨
    else:
        # general fallback
        if ARTICLE_RE.search(full_text):
            return _chunk_by_articles(full_text, page_map)
        return _chunk_general(full_text, page_map)


def _chunk_by_articles(full_text, page_map):
    """계약서 Parent-Child 청킹"""
    chunks = []
    boundaries = [m.start() for m in ARTICLE_RE.finditer(full_text) if m.start() > 0]

    if not boundaries:
        return _chunk_general(full_text, page_map)

    # header (before first article) -> standalone
    header = full_text[:boundaries[0]].strip()
    if header:
        chunks.append(_make_chunk(header, 0, boundaries[0], 'header', None, '계약 정보', page_map, role='standalone'))

    segments = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(full_text)
        seg = full_text[start:end].strip()
        if seg:
            segments.append((start, end, seg))

    for seg_start, seg_end, seg in segments:
        art_num, art_title = _extract_article_info(seg)
        ctype = 'annex' if ANNEX_RE.search(seg) else 'article'

        # Parent 청크 생성
        parent_chunk = _make_chunk(seg, seg_start, seg_end, ctype, art_num, art_title, page_map, role='parent')
        chunks.append(parent_chunk)

        # Child 청크 분할 (조항 내 세부 문장)
        child_chunks = _split_long_article_into_children(seg, seg_start, ctype, art_num, art_title, page_map)
        for child in child_chunks:
            child['parent_ref_index'] = len(chunks) - 1  # 같은 리스트 내 parent 인덱스 임시 저장
        
        chunks.extend(child_chunks)

    return chunks


def _split_long_article_into_children(text, base_offset, chunk_type, art_num, art_title, page_map):
    """Parent 조항을 잘게 쪼개어 Child 청크로 만듭니다."""
    boundaries = [m.start() for m in PARAGRAPH_RE.finditer(text) if m.start() > 0]
    
    # 세부 단락 기호가 없는 경우 단순 크기로 분할
    if not boundaries:
        chunks = []
        off = base_offset
        for sub in _split_text(text, CHUNK_SIZE_CONTRACT, CHUNK_OVERLAP_CONTRACT):
            chunks.append(_make_chunk(sub, off, off + len(sub), chunk_type, art_num, art_title, page_map, role='child'))
            off += len(sub)
        return chunks

    chunks = []
    group_start = 0
    group_text = ''
    for boundary in boundaries + [len(text)]:
        seg = text[group_start:boundary].strip()
        if not seg:
            continue
        if len(group_text) + len(seg) > CHUNK_SIZE_CONTRACT and group_text:
            off = base_offset + group_start
            chunks.append(_make_chunk(group_text, off, off + len(group_text), chunk_type, art_num, art_title, page_map, role='child'))
            group_text = seg
            group_start = boundary
        else:
            group_text = (group_text + '\n' + seg).strip() if group_text else seg
            
    if group_text:
        off = base_offset + group_start
        chunks.append(_make_chunk(group_text, off, off + len(group_text), chunk_type, art_num, art_title, page_map, role='child'))
        
    return chunks


def _chunk_by_markdown_headers(full_text, page_map):
    """보고서 마크다운 헤더 기반 청킹 (표는 찢어지지 않게 유지)"""
    chunks = []
    
    # H1~H3 헤더 기준 분할. (표시는 보존)
    header_pattern = re.compile(r'(?:^|\n)(#{1,3}\s+.*)', re.MULTILINE)
    
    boundaries = [m.start() for m in header_pattern.finditer(full_text)]
    if not boundaries:
        return _chunk_general(full_text, page_map)
        
    if boundaries[0] > 0:
        boundaries.insert(0, 0)
        
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(full_text)
        seg = full_text[start:end].strip()
        if not seg:
            continue
            
        first_line = seg.split('\n')[0].strip()
        title = first_line.lstrip('#').strip()[:50]
        
        # 섹션이 너무 길면 서브 분할 (단, 표 데이터는 분할 피함)
        if len(seg) > MAX_ARTICLE_SIZE * 2:
            offset = start
            for sub in _split_text_preserve_tables(seg, CHUNK_SIZE_GENERAL * 2, CHUNK_OVERLAP_GENERAL):
                chunks.append(_make_chunk(sub, offset, offset + len(sub), 'report', None, title, page_map, role='standalone'))
                offset += len(sub)
        else:
            chunks.append(_make_chunk(seg, start, end, 'report', None, title, page_map, role='standalone'))
            
    return chunks


def _split_text_preserve_tables(text, chunk_size, overlap):
    """표 데이터(|---|)를 보존하면서 텍스트를 분할"""
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []
        
    chunks = []
    lines = text.split('\n')
    
    current_chunk = []
    current_len = 0
    in_table = False
    
    for line in lines:
        if '|' in line and '--' in line: # table separator rough match
            in_table = True
        elif line.strip() == '' and in_table:
            in_table = False # empty line breaks table
            
        current_chunk.append(line)
        current_len += len(line) + 1
        
        if current_len >= chunk_size and not in_table:
            chunks.append('\n'.join(current_chunk).strip())
            # overlap handling is tricky with tables, simplified here
            current_chunk = current_chunk[-3:] # keep last 3 lines for context
            current_len = sum(len(l)+1 for l in current_chunk)
            
    if current_chunk:
        joined = '\n'.join(current_chunk).strip()
        if joined:
            chunks.append(joined)
            
    return chunks


def _chunk_general(full_text, page_map):
    """일반 청킹 (standalone)"""
    chunks = []
    offset = 0
    for sub in _split_text(full_text, CHUNK_SIZE_GENERAL, CHUNK_OVERLAP_GENERAL):
        first_line = sub.split('\n')[0].strip()
        title = first_line[:40] if len(first_line) < 50 else ''
        chunks.append(_make_chunk(sub, offset, offset + len(sub), 'general', None, title, page_map, role='standalone'))
        offset += len(sub)
    return chunks


def _make_chunk(content, char_start, char_end, chunk_type, article_number, article_title, page_map, role='standalone'):
    page_num = _find_page(char_start, page_map) if page_map else None
    sec = f'제{article_number}조' if article_number else (article_title[:40] if article_title else '')
    return {
        'content': content.replace('\x00', ''),  # NUL 문자 제거
        'page_number': page_num,
        'section_title': sec.replace('\x00', ''),
        'article_number': article_number,
        'article_title': article_title.replace('\x00', '') if article_title else '',
        'chunk_type': chunk_type,
        'char_start': char_start,
        'char_end': char_end,
        'chunk_role': role,
    }


def _extract_article_info(text):
    m = re.match(r'\s*제\s*(\d+)\s*조\s*[(\[（【]?([^)\]）】\n]{0,40})[)\]）】]?', text)
    if m:
        return int(m.group(1)), m.group(2).strip() if m.group(2) else ''
    return None, ''


def _build_page_map(pages_text):
    page_map = {}
    offset = 0
    for page_num, text in pages_text:
        page_map[offset] = page_num
        offset += len(text) + 2
    return page_map


def _find_page(char_pos, page_map):
    if not page_map:
        return None
    page = None
    for offset, pg in sorted(page_map.items()):
        if char_pos >= offset:
            page = pg
        else:
            break
    return page


def _split_text(text, chunk_size, overlap):
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ['\n\n', '\n', '. ', '。', ' ']:
                last_sep = text.rfind(sep, start + chunk_size // 2, end)
                if last_sep != -1:
                    end = last_sep + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks
