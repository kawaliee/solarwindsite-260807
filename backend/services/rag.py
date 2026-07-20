"""
RAG 검색 서비스
질문 → 임베딩 → Qdrant 검색 → 컨텍스트 구성 → 출처 매핑
"""
import logging

logger = logging.getLogger(__name__)


def search_documents(query: str, project_id: str = None, top_k: int = 15) -> dict:
    """
    RAG 검색 파이프라인
    1. 질문 임베딩
    2. Qdrant 하이브리드 검색
    3. 컨텍스트 구성
    4. 출처 데이터 구성

    Returns:
        {
            'context': str,      # LLM에 전달할 컨텍스트
            'sources': list,     # 출처 데이터 (UI 칩용)
        }
    """
    # 1. 쿼리 분석 → 메타데이터 필터 조건 추출
    try:
        from services.query_analyzer import analyze_query
        query_analysis = analyze_query(query)
        sub_type_val = query_analysis.get('sub_type')
        if sub_type_val == 'O&M':
            sub_type_val = 'OM'

        metadata_filters = {
            'project_names': query_analysis.get('project_names', []),
            'document_type': query_analysis.get('document_type'),
            'sub_type': sub_type_val,
        }
        # 필터가 전부 비어있으면 None으로 설정
        if not any(metadata_filters.values()):
            metadata_filters = None
    except Exception as e:
        logger.warning(f'Query analysis failed: {e}')
        metadata_filters = None

    # ── 경량 멀티쿼리 검색 ──
    # 복합 질문("가격+보증")을 한 벡터로 임베딩하면 가격/단가 청크가 보증 어휘에 희석되어
    # Qdrant 순위 100위 밖으로 밀린다. (실측: '원/kWh 거래단가' 토큰을 넣으면 8위, 빼면 탈락)
    # → 가격 국면 서브쿼리를 분리하고 프로젝트 정식 법인명(SPC)을 주입해 결과를 합집합한다.
    PROJECT_SPC_SYNONYMS = {
        '태평': '신안증도태양광 신안증도',
        '당진 1단계': '당진행복솔라',
        '당진 2단계': '당진행복솔라 대호솔라',
        '홍성': '홍성빛나래솔라 홍성빛나래',
    }
    query_lower = query.lower()
    proj_syn = ''
    if metadata_filters and metadata_filters.get('project_names'):
        proj_syn = ' '.join(
            PROJECT_SPC_SYNONYMS.get(p, '') for p in metadata_filters['project_names']
        ).strip()

    # 공통(영문/일반/보증) 확장 — 베이스 서브쿼리
    base_ext = query
    if '금리' in query_lower or '이자' in query_lower:
        base_ext += " interest rate fees"
    if '대주단' in query_lower or '대주' in query_lower:
        base_ext += " lenders lender"
    if '대출금액' in query_lower or '대출' in query_lower or '차입' in query_lower:
        base_ext += " loan amount financing debt"
    if '상대방' in query_lower or '당사자' in query_lower or '계약자' in query_lower:
        base_ext += " 갑지 계약당사자 체결 발주자 이하"
    if '보증' in query_lower or '보장' in query_lower or '발전시간' in query_lower:
        base_ext += " 보장공급량 보장 감소기준 발전시간 배상"
    if 'ppa' in query_lower:
        base_ext += " 재생에너지 전력거래 virtual 직접 ppa"

    subqueries = [f"{base_ext} {proj_syn}".strip()]
    price_hit = any(k in query_lower for k in ['가격', '단가', '계약금액', '공사비', 'ppa', '정산', '금액'])
    if price_hit:
        # 가격/단가 청크는 '거래단가·기준가격·원/kWh' 토큰에 강하게 반응(실측)
        subqueries.append(
            f"{query} {proj_syn} 직접 PPA Virtual PPA 거래단가 기준가격 계약단가 정산단가 원/kWh".strip()
        )

    try:
        from services.embedding import get_single_embedding, get_single_sparse_embedding
        from services.qdrant_service import search
    except Exception as e:
        logger.warning(f'Embedding/search import failed: {e}')
        return {'context': '', 'sources': []}

    SUB_TOP_K = 60
    merged = {}
    for sq in subqueries:
        try:
            qd = get_single_embedding(sq)
            qs = get_single_sparse_embedding(sq)
            if qd is None or qs is None:
                continue
            res = search(
                qd, qs,
                project_id=project_id,
                metadata_filters=metadata_filters,
                top_k=SUB_TOP_K,
            )
        except Exception as e:
            logger.warning(f"Sub-query search failed: {e}")
            continue
        for r in res:
            pid = str(r.id)
            if pid not in merged or (r.score or 0) > (merged[pid].score or 0):
                merged[pid] = r

    raw_results = sorted(merged.values(), key=lambda x: (x.score or 0), reverse=True)
    if not raw_results:
        return {'context': '', 'sources': []}

    # 3. Parent-Child 매핑 (Child 청크 매칭 시 Parent 청크로 대체)
    # 딕셔너리로 변환하면서 고유한 Parent 또는 Standalone 청크만 수집 + 동일 문서 독점 방지(다양성 확보)
    unique_candidates = {}
    doc_chunk_counters = {}
    # 동일 문서 내에 금리, 대주단, 대출금액이 서로 다른 청크(예: 본문 vs 별첨)에 흩어져 존재하므로
    # 한 문서에서 최대 10개 청크까지 넉넉하게 참고할 수 있도록 제한을 확장함 (다양성은 Reranker에 위임)
    MAX_CHUNKS_PER_DOC = 10
    
    from apps.documents.models import DocumentChunk, Document
    
    # DB에 남아있는 유효한 document_id만 필터링 (Qdrant 고아 청크 방지)
    doc_ids = [res.payload.get('document_id') for res in raw_results if res.payload.get('document_id')]
    valid_doc_ids = set(Document.objects.filter(id__in=doc_ids).values_list('id', flat=True))
    valid_doc_ids = {str(did) for did in valid_doc_ids}
    
    for result in raw_results:
        payload = result.payload
        doc_id = payload.get('document_id')
        
        # 유효하지 않은(삭제된) 문서는 제외
        if str(doc_id) not in valid_doc_ids:
            continue
            
        role = payload.get('chunk_role', 'standalone')
        
        chunk_id = payload.get('chunk_id')
        parent_id = payload.get('parent_chunk_id')
        
        # 고유 키: parent가 있으면 parent_id, 아니면 본인 id
        key = parent_id if (role == 'child' and parent_id) else chunk_id
        
        if key not in unique_candidates:
            # 동일 문서에서 가져올 수 있는 최대 청크 개수를 제한하여 검색 문서의 다양성을 증진함
            doc_chunk_counters[doc_id] = doc_chunk_counters.get(doc_id, 0)
            if doc_chunk_counters[doc_id] >= MAX_CHUNKS_PER_DOC:
                continue
            doc_chunk_counters[doc_id] += 1
            # Child인 경우 Parent 내용을 DB에서 가져와서 교체
            if role == 'child' and parent_id:
                try:
                    parent_db = DocumentChunk.objects.filter(qdrant_point_id=parent_id).first()
                    if parent_db:
                        # Parent 내용으로 덮어씌움
                        payload['content'] = parent_db.content
                        payload['chunk_role'] = 'parent'
                        # ID 정보 교체
                        payload['chunk_id'] = str(parent_db.id)
                except Exception as e:
                    logger.warning(f'Parent chunk fetch failed: {e}')
            
            # 검색 점수는 가장 높은 Child의 점수를 그대로 Parent가 승계함
            payload['hybrid_score'] = result.score 
            unique_candidates[key] = payload

    candidate_list = list(unique_candidates.values())

    # Reranker 지연을 줄이기 위해 hybrid_score 상위 RERANK_POOL개만 재랭킹 대상으로 캡한다.
    # (CPU Cross-Encoder는 후보 수에 비례해 느리므로 무작정 넓히지 않는다.)
    # Tier3: 가격 질문이면 topic_role='가격' 청크는 캡과 무관하게 반드시 재랭킹 대상에 포함해
    #        리랭커가 실제 관련성으로 승격할 기회를 보장한다.
    # CPU Cross-Encoder는 쌍당 ~2.3초(하한)라 지연이 후보 수에 선형 비례한다.
    # 컨텍스추얼 검색으로 1차 recall이 좋아졌으므로 재랭킹 대상을 14개로 좁혀 지연을 줄이되,
    # 가격 질의면 topic_role='가격' 청크(최대 5개)를 반드시 포함해 품질을 지킨다.
    RERANK_POOL = 14
    candidate_list.sort(key=lambda p: (p.get('hybrid_score') or 0), reverse=True)
    if price_hit:
        price_c = [p for p in candidate_list if p.get('topic_role') == '가격']
        rest = [p for p in candidate_list if p.get('topic_role') != '가격']
        candidate_list = (price_c[:5] + rest)[:RERANK_POOL]
    else:
        candidate_list = candidate_list[:RERANK_POOL]

    # 4. Reranker 적용 (최상위 top_k 추출)
    try:
        from services.reranker import rerank_results
        final_results = rerank_results(query, candidate_list, top_k=top_k)
    except Exception as e:
        logger.warning(f'Reranking failed: {e}')
        final_results = candidate_list[:top_k]

    # 5. 컨텍스트 및 출처 데이터 구성
    context_parts = []
    sources = []
    total_context_chars = 0
    MAX_CONTEXT_CHARS = 25000  # 분당 토큰 제한(TPM) 초과 예방용 글자 수 상한

    for i, payload in enumerate(final_results):
        content = payload.get('content', '')
        doc_title = payload.get('document_title', '')
        page_num = payload.get('page_number')
        section = payload.get('section_title', '')
        art_num = payload.get('article_number')
        art_title = payload.get('article_title')
        chunk_type = payload.get('chunk_type', 'general')

        # 컨텍스트 위치 라벨 구성
        location_parts = []
        if page_num:
            location_parts.append(f'p.{page_num}')
        if art_num:
            location_parts.append(f'제{art_num}조')
        if art_title and chunk_type != 'article':
            location_parts.append(art_title)
        elif section and not art_num:
            location_parts.append(section)
            
        location = ' · '.join(location_parts)

        # 개별 청크당 최대 유입 한도를 제한하여 다양성 및 대형 청크로 인한 후속 청크 유실 방지
        MAX_CHUNK_CHARS_IN_CONTEXT = 3000
        safe_content = content
        if len(safe_content) > MAX_CHUNK_CHARS_IN_CONTEXT:
            safe_content = safe_content[:MAX_CHUNK_CHARS_IN_CONTEXT] + "\n... (이하 대형 데이터 생략)"

        chunk_text = (
            f"[출처 {i+1}: {doc_title}" +
            (f" ({location})" if location else "") +
            f"]\n{safe_content}"
        )

        if total_context_chars + len(chunk_text) <= MAX_CONTEXT_CHARS:
            context_parts.append(chunk_text)
            total_context_chars += len(chunk_text)
        else:
            # 남은 공간이 있다면 남은 한도만큼 슬라이싱해 주입
            remaining_space = MAX_CONTEXT_CHARS - total_context_chars
            if remaining_space > 200:
                sliced_content = safe_content[:remaining_space - 100] + "\n... (한도 제한으로 일부 생략)"
                sliced_chunk_text = (
                    f"[출처 {i+1}: {doc_title}" +
                    (f" ({location})" if location else "") +
                    f"]\n{sliced_content}"
                )
                context_parts.append(sliced_chunk_text)
                total_context_chars += len(sliced_chunk_text)
            logger.info(f"RAG context character limit ({MAX_CONTEXT_CHARS}) reached. Skipping chunk {i+1} for LLM prompt.")

        # 출처 데이터 (UI 칩용 - Reranker 상위 10개 표시는 유지)
        short_label = doc_title[:4] + '…' if len(doc_title) > 4 else doc_title
        sources.append({
            'document_id': payload.get('document_id'),
            'chunk_id': payload.get('chunk_id'),
            'display_title': doc_title,
            'short_label': short_label,
            'page_number': page_num,
            'location_label': location,
            'score': payload.get('rerank_score', payload.get('hybrid_score', 0)),
            'snippet': content[:200],
        })

    # 프로젝트 별칭(Alias)과 법인 SPC명 간의 매핑 가이드를 상단에 결합하여 LLM이 명칭 불일치로 인한 오판을 방지하게 함
    alias_guide = (
        "[프로젝트 - 공식 법인 SPC명 동의어 사전]\n"
        "- 태평 PJT = 주식회사 신안증도태양광 (신안증도)\n"
        "- 당진 PJT = 주식회사 당진행복솔라\n"
        "- 홍성 PJT = 주식회사 홍성빛나래솔라 (홍성빛나래)\n\n"
    )
    context = alias_guide + '\n\n'.join(context_parts)

    return {
        'context': context,
        'sources': sources,
    }


def search_contracts_for_drafting(template_code: str, key_terms: dict, top_k: int = 5) -> dict:
    """
    계약서 초안 생성을 위한 RAG 검색 (K-1 고도화)
    실제 DB에 적재된 계약서를 찾아 LLM 프롬프트에 주입할 컨텍스트를 구성합니다.
    """
    # 1. template_code -> Qdrant sub_type 매핑 (DB 보유 현황 기준)
    code_to_subtype = {
        'EPC': 'EPC',
        'OM': 'O&M',
        'PPA_REC': 'PPA', # PPA, REC 모두 포괄 가능
        'SPA': '지분매각(SPA, SHA, 증설합의서)',
        'SHA': '지분매각(SPA, SHA, 증설합의서)',
        'FIN': 'PF',
    }
    
    sub_type = code_to_subtype.get(template_code, '')
    
    metadata_filters = {
        'document_type': 'contract',
    }
    if sub_type:
        metadata_filters['sub_type'] = sub_type
        
    # 2. key_terms를 기반으로 자연어 쿼리 생성
    query_parts = []
    for k, v in key_terms.items():
        if isinstance(v, str) and v.strip():
            query_parts.append(v.strip())
            
    query = f"{template_code} 계약 " + " ".join(query_parts)[:300] # 너무 길면 잘라냄
    
    # 3. 임베딩 (Dense + Sparse)
    try:
        from services.embedding import get_single_embedding, get_single_sparse_embedding
        query_dense = get_single_embedding(query)
        query_sparse = get_single_sparse_embedding(query)
    except Exception as e:
        logger.warning(f'Contract drafting embedding failed: {e}')
        return {'context': '', 'sources': []}
        
    # 4. Qdrant 검색
    try:
        from services.qdrant_service import search
        raw_results = search(
            query_dense, query_sparse,
            metadata_filters=metadata_filters,
            top_k=top_k * 2 # Child -> Parent 대체를 감안하여 넉넉히 가져옴
        )
    except Exception as e:
        logger.warning(f'Contract drafting Qdrant search failed: {e}')
        return {'context': '', 'sources': []}

    if not raw_results:
        return {'context': '', 'sources': []}
        
    # 5. Parent-Child 매핑 로직 적용
    unique_candidates = {}
    from apps.documents.models import DocumentChunk, Document
    
    doc_ids = [res.payload.get('document_id') for res in raw_results if res.payload.get('document_id')]
    valid_doc_ids = set(Document.objects.filter(id__in=doc_ids).values_list('id', flat=True))
    valid_doc_ids = {str(did) for did in valid_doc_ids}
    
    for result in raw_results:
        payload = result.payload
        doc_id = payload.get('document_id')
        if str(doc_id) not in valid_doc_ids:
            continue
            
        role = payload.get('chunk_role', 'standalone')
        chunk_id = payload.get('chunk_id')
        parent_id = payload.get('parent_chunk_id')
        
        key = parent_id if (role == 'child' and parent_id) else chunk_id
        
        if key not in unique_candidates:
            if role == 'child' and parent_id:
                try:
                    parent_obj = DocumentChunk.objects.get(id=parent_id)
                    payload['content'] = parent_obj.content
                    payload['chunk_role'] = 'parent'
                    payload['chunk_id'] = str(parent_obj.id)
                except DocumentChunk.DoesNotExist:
                    pass
                    
            unique_candidates[key] = {
                'payload': payload,
                'score': result.score,
            }

    # 스코어 순 정렬 후 Top K
    sorted_candidates = sorted(unique_candidates.values(), key=lambda x: x['score'], reverse=True)[:top_k]
    
    context_parts = []
    sources = []
    
    for c in sorted_candidates:
        payload = c['payload']
        doc_title = payload.get('original_filename') or payload.get('document_title', 'Unknown Contract')
        content = payload.get('content', '')
        
        context_parts.append(f"[실제 계약서 참조 조항 - 문서명: {doc_title}]\n{content}")
        
        sources.append({
            'document_id': payload.get('document_id'),
            'chunk_id': payload.get('chunk_id'),
            'display_title': doc_title,
            'score': round(c['score'], 4),
        })

    return {
        'context': '\n\n---\n\n'.join(context_parts),
        'sources': sources,
    }


def search_clause_examples(template_code: str, article_type: str, project_id: str = None, top_k: int = 3) -> list:
    """
    특정 조항 타입(article_type)에 매핑되는 실제 조항 사례 2~3건 검색 (RAG 고도화)
    권한 검증 및 마스킹을 고려하여 parent 청크 단위로 리스트를 반환합니다.
    """
    # 1. template_code -> Qdrant sub_type 매핑 (DB 보유 현황 기준)
    code_to_subtype = {
        'EPC': 'EPC',
        'OM': 'O&M',
        'PPA_REC': 'PPA',
        'SPA': '지분매각(SPA, SHA, 증설합의서)',
        'SHA': '지분매각(SPA, SHA, 증설합의서)',
        'FIN': 'PF',
    }
    
    sub_type = code_to_subtype.get(template_code, '')
    
    # Qdrant 필터 조건 구성
    from qdrant_client.models import FieldCondition, MatchValue, Filter
    
    must_conditions = [
        FieldCondition(key='document_type', match=MatchValue(value='contract')),
        FieldCondition(key='chunk_role', match=MatchValue(value='parent')) # 원본 parent 조항만 추출
    ]
    if sub_type:
        must_conditions.append(FieldCondition(key='sub_type', match=MatchValue(value=sub_type)))
    
    # article_type 필터링
    must_conditions.append(FieldCondition(key='article_type', match=MatchValue(value=article_type)))
    
    if project_id:
        must_conditions.append(FieldCondition(key='project_id', match=MatchValue(value=project_id)))
        
    filter_conditions = Filter(must=must_conditions)
    
    # 2. RAG 검색용 자연어 쿼리
    query = f"{template_code} 계약 {article_type} 조항"
    
    # 3. 임베딩 (Dense + Sparse)
    try:
        from services.embedding import get_single_embedding, get_single_sparse_embedding
        query_dense = get_single_embedding(query)
        query_sparse = get_single_sparse_embedding(query)
    except Exception as e:
        logger.warning(f'Clause drafting embedding failed: {e}')
        return []
        
    # 4. Qdrant RRF 하이브리드 검색 실행
    try:
        from services.qdrant_service import _get_client
        from qdrant_client.models import SparseVector
        from django.conf import settings
        
        client = _get_client()
        if not client:
            return []
            
        # 계약서 조항 RAG이므로 Sparse(키워드/조항번호) 비중을 60%로 상향하여 정확한 매칭 도출
        w_dense = 0.40
        w_sparse = 0.60

        # Dense 쿼리
        dense_response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=query_dense,
            using="dense",
            query_filter=filter_conditions,
            limit=top_k * 3,
            with_payload=True
        )
        dense_points = dense_response.points

        # Sparse 쿼리
        sparse_response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=SparseVector(
                indices=query_sparse['indices'],
                values=query_sparse['values']
            ),
            using="sparse",
            query_filter=filter_conditions,
            limit=top_k * 3,
            with_payload=True
        )
        sparse_points = sparse_response.points

        # RRF 결합 적용
        k_const = 60
        scores = {}

        for rank, p in enumerate(dense_points):
            scores[str(p.id)] = {
                'point': p,
                'dense_rank': rank + 1,
                'sparse_rank': None
            }

        for rank, p in enumerate(sparse_points):
            pid_str = str(p.id)
            if pid_str in scores:
                scores[pid_str]['sparse_rank'] = rank + 1
            else:
                scores[pid_str] = {
                    'point': p,
                    'dense_rank': None,
                    'sparse_rank': rank + 1
                }

        merged_points = []
        for pid_str, info in scores.items():
            dense_contrib = (1 / (k_const + info['dense_rank'])) if info['dense_rank'] is not None else 0
            sparse_contrib = (1 / (k_const + info['sparse_rank'])) if info['sparse_rank'] is not None else 0
            
            weighted_score = (w_dense * dense_contrib) + (w_sparse * sparse_contrib)
            
            p = info['point']
            p.score = weighted_score
            merged_points.append(p)

        merged_points.sort(key=lambda x: x.score, reverse=True)
        points = merged_points[:top_k]
        
    except Exception as e:
        logger.warning(f'Clause Qdrant query failed: {e}. Fallback to DB query.')
        points = []

    # Qdrant 결과가 비어있거나 실패한 경우 DB Fallback 검색
    if not points:
        try:
            from apps.documents.models import DocumentChunk
            qs = DocumentChunk.objects.filter(
                document_type='contract',
                chunk_role='parent',
                metadata__article_type=article_type
            )
            if sub_type:
                qs = qs.filter(sub_type=sub_type)
            if project_id:
                qs = qs.filter(document__project_id=project_id)
                
            db_results = list(qs[:top_k])
            return [
                {
                    'content': db.content,
                    'article_type': article_type,
                    'display_title': db.document.title,
                    'bias_tag': db.metadata.get('bias_tag', 'neutral'),
                    'governing_law': db.metadata.get('governing_law', '대한민국'),
                    'language': db.metadata.get('language', 'ko'),
                    'score': 1.0
                }
                for db in db_results
            ]
        except Exception as dbe:
            logger.error(f'Clause DB search fallback failed: {dbe}')
            return []

    # 5. 권한 검증 및 결과 정규화 리스트 작성
    from apps.documents.models import Document
    doc_ids = [res.payload.get('document_id') for res in points if res.payload.get('document_id')]
    valid_doc_ids = set(Document.objects.filter(id__in=doc_ids).values_list('id', flat=True))
    valid_doc_ids = {str(did) for did in valid_doc_ids}
    
    clause_list = []
    for item in points:
        payload = item.payload
        doc_id = payload.get('document_id')
        if str(doc_id) not in valid_doc_ids:
            continue # 삭제되었거나 접근 불가능한 계약서 패스
            
        clause_list.append({
            'content': payload.get('content', ''),
            'article_type': payload.get('article_type', article_type),
            'display_title': payload.get('document_title', '참조 계약서'),
            'bias_tag': payload.get('bias_tag', 'neutral'),
            'governing_law': payload.get('governing_law', '대한민국'),
            'language': payload.get('language', 'ko'),
            'score': round(item.score if hasattr(item, 'score') else 1.0, 4)
        })
        
    return clause_list
