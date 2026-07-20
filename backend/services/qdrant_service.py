"""
Qdrant 벡터DB 서비스
- 컬렉션 관리
- 청크 업서트 (Dense + Sparse Hybrid)
- 하이브리드 검색 (Reciprocal Rank Fusion)
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Qdrant 클라이언트 (싱글턴)"""
    global _client
    if _client is not None:
        return _client

    try:
        from qdrant_client import QdrantClient
        _client = QdrantClient(url=settings.QDRANT_URL)
        logger.info(f'Qdrant connected: {settings.QDRANT_URL}')
        return _client
    except Exception as e:
        logger.error(f'Qdrant connection failed: {e}')
        return None


def ensure_collection():
    """re_documents 컬렉션 생성 (Named Vectors for Hybrid) + Payload Index"""
    client = _get_client()
    if client is None:
        return

    try:
        from qdrant_client.models import VectorParams, Distance, SparseVectorParams, SparseIndexParams, PayloadSchemaType
        collections = [c.name for c in client.get_collections().collections]

        if settings.QDRANT_COLLECTION not in collections:
            client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config={
                    "dense": VectorParams(
                        size=1024,  # BGE-M3 dense
                        distance=Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(
                            on_disk=False,
                        )
                    )
                }
            )
            logger.info(f'Hybrid Collection created: {settings.QDRANT_COLLECTION}')
            
            # 검색 속도를 위한 Payload Index 추가
            for field in ['project_name', 'document_type', 'sub_type', 'chunk_role', 'parent_chunk_id', 'article_type', 'governing_law', 'language', 'bias_tag']:
                try:
                    client.create_payload_index(
                        collection_name=settings.QDRANT_COLLECTION,
                        field_name=field,
                        field_schema=PayloadSchemaType.KEYWORD
                    )
                except Exception as e:
                    logger.warning(f'Payload index creation failed for {field}: {e}')

    except Exception as e:
        logger.error(f'Collection creation failed: {e}')


def upsert_chunks(document, chunk_objects, dense_embeddings, sparse_embeddings):
    """청크를 Qdrant에 업서트 (Dense + Sparse) - Batch 처리로 제한 초과 방지"""
    client = _get_client()
    if client is None:
        logger.warning('Qdrant not available, skipping upsert')
        return

    ensure_collection()

    from qdrant_client.models import PointStruct, SparseVector

    points = []
    for chunk, dense, sparse in zip(chunk_objects, dense_embeddings, sparse_embeddings):
        if dense is None or sparse is None:
            continue

        points.append(PointStruct(
            id=str(chunk.qdrant_point_id),
            vector={
                "dense": dense,
                "sparse": SparseVector(
                    indices=sparse['indices'],
                    values=sparse['values']
                )
            },
            payload={
                'chunk_id': str(chunk.id),
                'document_id': str(document.id),
                'project_id': str(document.project_id) if document.project_id else None,
                'is_global': document.project_id is None,
                'document_title': document.title,
                'page_number': chunk.page_number,
                'section_title': chunk.section_title,
                'article_number': chunk.metadata.get('article_number') if chunk.metadata else None,
                'article_title': chunk.metadata.get('article_title', '') if chunk.metadata else '',
                
                # 조항 RAG 분류용 메타데이터
                'topic_role': chunk.metadata.get('topic_role', '일반') if chunk.metadata else '일반',
                'article_type': chunk.metadata.get('article_type', '기타') if chunk.metadata else '기타',
                'governing_law': chunk.metadata.get('governing_law', '대한민국') if chunk.metadata else '대한민국',
                'language': chunk.metadata.get('language', 'ko') if chunk.metadata else 'ko',
                'bias_tag': chunk.metadata.get('bias_tag', 'neutral') if chunk.metadata else 'neutral',
                'industry': chunk.metadata.get('industry', '일반') if chunk.metadata else '일반',
                
                # 경로 기반 확장 메타데이터
                'project_name': chunk.project_name,
                'spc_name': chunk.spc_name,
                'document_type': chunk.document_type,
                'sub_type': chunk.sub_type,
                
                # Parent-Child 구조 필드
                'chunk_role': chunk.chunk_role,
                'parent_chunk_id': str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                
                'file_type': document.file_type,
                'content': chunk.content, # 프론트 출력 및 LLM 컨텍스트 위해 500자 제한 해제 (또는 3000자 제한)
            }
        ))

    if points:
        try:
            # 32MB payload limit 방지를 위해 100개씩 배치 분할 업서트
            batch_size = 100
            for i in range(0, len(points), batch_size):
                batch_points = points[i:i + batch_size]
                client.upsert(
                    collection_name=settings.QDRANT_COLLECTION,
                    points=batch_points,
                )
            logger.info(f'Upserted {len(points)} hybrid points for document {document.id} in batches.')
        except Exception as e:
            logger.error(f'Qdrant upsert failed: {e}')


def search(dense_query, sparse_query, project_id: str = None,
           metadata_filters: dict = None, top_k: int = 6) -> list:
    """
    하이브리드 검색 (RRF) + 메타데이터 Pre-filtering
    - project_id: 특정 프로젝트 범위 검색 (+ 전사 공용)
    - metadata_filters: 쿼리 분석기가 추출한 메타데이터 필터
        {
            'project_names': ['당진'],
            'document_type': 'contract',
            'sub_type': 'EPC',
        }
    - top_k: 반환할 결과 수
    """
    client = _get_client()
    if client is None:
        return []

    if dense_query is None or sparse_query is None:
        return []

    from qdrant_client.models import (
        Filter, FieldCondition, MatchValue, MatchAny,
        Prefetch, SparseVector
    )

    # ── 필터 조건 빌드 ──
    must_conditions = []

    # 1) 쿼리 분석기 기반 메타데이터 필터 (Pre-filtering)
    if metadata_filters:
        project_names = metadata_filters.get('project_names', [])
        doc_type = metadata_filters.get('document_type')
        # sub_type은 인덱싱 시 누락 가능(한글+영문 결합 파일명 등)하므로
        # hard 필터로 사용하지 않고, 임베딩의 의미 매칭에 위임한다.

        if project_names:
            must_conditions.append(
                FieldCondition(key='project_name', match=MatchAny(any=project_names))
            )

        if doc_type:
            must_conditions.append(
                FieldCondition(key='document_type', match=MatchValue(value=doc_type))
            )

    # 2) 기존 project_id 기반 필터 (UI에서 프로젝트를 선택한 경우)
    if project_id and not must_conditions:
        must_conditions.append(
            FieldCondition(key='project_id', match=MatchValue(value=project_id))
        )

    filter_conditions = Filter(must=must_conditions) if must_conditions else None

    try:
        # 1. 문서 유형에 따라 Dense/Sparse RRF 가중치(Weights) 차등 결정
        doc_type = metadata_filters.get('document_type') if metadata_filters else None
        if doc_type == 'contract':
            w_dense = 0.40
            w_sparse = 0.60
            logger.info(f"Custom RRF weights applied for CONTRACT: dense={w_dense}, sparse={w_sparse}")
        elif doc_type == 'report':
            w_dense = 0.70
            w_sparse = 0.30
            logger.info(f"Custom RRF weights applied for REPORT: dense={w_dense}, sparse={w_sparse}")
        else:
            w_dense = 0.50
            w_sparse = 0.50
            logger.info(f"Custom RRF weights applied for GENERAL: dense={w_dense}, sparse={w_sparse}")

        # 2. Dense 쿼리 실행
        dense_response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=dense_query,
            using="dense",
            query_filter=filter_conditions,
            limit=top_k * 3,
            with_payload=True
        )
        dense_points = dense_response.points

        # 3. Sparse 쿼리 실행
        from qdrant_client.models import SparseVector
        sparse_response = client.query_points(
            collection_name=settings.QDRANT_COLLECTION,
            query=SparseVector(
                indices=sparse_query['indices'],
                values=sparse_query['values']
            ),
            using="sparse",
            query_filter=filter_conditions,
            limit=top_k * 3,
            with_payload=True
        )
        sparse_points = sparse_response.points

        # 4. RRF 결합 공식 적용
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
            
            # [지능형 랭킹 부스트 가드]
            # Dense나 Sparse 중 어느 한쪽에서 15위 이내의 압도적인 검색 정합성을 기록했다면
            # 다른 한쪽의 점수가 0점이더라도 최종 후보군에서 누락되지 않도록 최소 융합 점수(Boost)를 보증함
            if (info['dense_rank'] is not None and info['dense_rank'] <= 15) or \
               (info['sparse_rank'] is not None and info['sparse_rank'] <= 15):
                weighted_score += 0.050  # 최상위 랭킹 독보적 청크에 대한 강력한 부스트 보증
            
            p = info['point']
            p.score = weighted_score
            merged_points.append(p)

        merged_points.sort(key=lambda x: x.score, reverse=True)
        points = merged_points[:top_k]

        # 5. Pre-filtered 결과 부족 시 폴백 (필터 해제 후 RRF 재실행)
        if metadata_filters and len(points) < 3:
            logger.info(f'Pre-filtered 결과 부족({len(points)}개), 필터 없이 재검색')
            fallback_dense = client.query_points(
                collection_name=settings.QDRANT_COLLECTION,
                query=dense_query,
                using="dense",
                query_filter=None,
                limit=top_k * 2,
                with_payload=True
            ).points

            fallback_sparse = client.query_points(
                collection_name=settings.QDRANT_COLLECTION,
                query=SparseVector(
                    indices=sparse_query['indices'],
                    values=sparse_query['values']
                ),
                using="sparse",
                query_filter=None,
                limit=top_k * 2,
                with_payload=True
            ).points

            fallback_scores = {}
            for rank, p in enumerate(fallback_dense):
                fallback_scores[str(p.id)] = {
                    'point': p,
                    'dense_rank': rank + 1,
                    'sparse_rank': None
                }
            for rank, p in enumerate(fallback_sparse):
                pid_str = str(p.id)
                if pid_str in fallback_scores:
                    fallback_scores[pid_str]['sparse_rank'] = rank + 1
                else:
                    fallback_scores[pid_str] = {
                        'point': p,
                        'dense_rank': None,
                        'sparse_rank': rank + 1
                    }

            fallback_merged = []
            for pid_str, info in fallback_scores.items():
                dense_contrib = (1 / (k_const + info['dense_rank'])) if info['dense_rank'] is not None else 0
                sparse_contrib = (1 / (k_const + info['sparse_rank'])) if info['sparse_rank'] is not None else 0
                weighted_score = (0.5 * dense_contrib) + (0.5 * sparse_contrib)
                
                p = info['point']
                p.score = weighted_score
                fallback_merged.append(p)

            fallback_merged.sort(key=lambda x: x.score, reverse=True)

            seen_ids = {str(p.id) for p in points}
            for p in fallback_merged:
                if str(p.id) not in seen_ids:
                    points.append(p)
                    seen_ids.add(str(p.id))
                    if len(points) >= top_k:
                        break

        return points[:top_k]
    except Exception as e:
        logger.error(f'Qdrant hybrid search failed: {e}')
        return []

