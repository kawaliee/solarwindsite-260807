"""
검색 품질 향상을 위한 Cross-Encoder 기반 Reranker 서비스
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_reranker_model = None


def _get_reranker_model():
    """Reranker 모델 지연 로딩 (메모리 절약 및 서버 시작 지연 방지)"""
    global _reranker_model
    if _reranker_model is None:
        try:
            from sentence_transformers import CrossEncoder
            # 다국어 지원 BGE-Reranker-v2-M3 모델 사용
            logger.info("Reranker 모델(BAAI/bge-reranker-v2-m3)을 메모리에 로드 중...")
            _reranker_model = CrossEncoder('BAAI/bge-reranker-v2-m3')
            logger.info("Reranker 모델 로드 완료")
        except Exception as e:
            logger.error(f"Reranker 로드 실패: {e}")
            return None
    return _reranker_model


def rerank_results(query: str, chunks: list, top_k: int = 5) -> list:
    """
    Qdrant에서 반환된 상위 후보 chunk들을 대상으로
    Cross-Encoder를 통과시켜 점수를 다시 매기고 정렬한다.

    Args:
        query: 사용자 질문
        chunks: Qdrant 반환 결과 (payload 딕셔너리 리스트 등 텍스트를 가진 객체)
        top_k: 최종 반환할 개수

    Returns:
        재정렬된 chunk 리스트
    """
    model = _get_reranker_model()
    if not model or not chunks:
        # 모델 로드 실패나 결과가 없으면 원래 순서대로 반환
        return chunks[:top_k]

    try:
        # Cross-Encoder 입력 구성: [(query, doc1), (query, doc2), ...]
        # Qdrant PointStruct의 payload를 사용한다고 가정
        pairs = []
        for chunk in chunks:
            # chunk가 payload dict인 경우
            content = chunk.get('content', '') if isinstance(chunk, dict) else getattr(chunk, 'content', '')
            # Parent 청크의 세부 정보와 숫자가 유실되지 않도록 1500자로 확장하여 Rerank 연산 수행
            pairs.append((query, content[:1500]))

        # 예측 스코어 계산
        scores = model.predict(pairs)

        # (점수, chunk) 튜플 리스트로 묶기 (Qdrant 부스트 가드 반영)
        scored_chunks = []
        for score, chunk in zip(scores, chunks):
            if isinstance(chunk, dict):
                qdrant_score = chunk.get('hybrid_score') or chunk.get('score') or 0.0
            else:
                qdrant_score = getattr(chunk, 'hybrid_score', None) or getattr(chunk, 'score', 0.0) or 0.0
                
            final_score = float(score)
            
            # [지능형 Reranker 랭킹 복원 가드]
            # Qdrant 단계에서 최상위 랭킹 부스트를 받았던 중요 청크(예: 대주단/출자금 표 요약)는
            # Reranker가 기호/표 구조의 한계로 점수를 박하게 주었더라도 최종 검색 컨텍스트에서 탈락하지 않도록
            # Rerank 예측 스코어에 강력한 부스트를 인가함
            if qdrant_score >= 0.050:
                final_score += 5.0
                
            scored_chunks.append((final_score, chunk))
            
        scored_chunks.sort(key=lambda x: x[0], reverse=True)

        # 상위 top_k개 반환 (점수 정보는 chunk_score라는 임시 필드에 추가)
        final_chunks = []
        for score, chunk in scored_chunks[:top_k]:
            if isinstance(chunk, dict):
                chunk['rerank_score'] = float(score)
            final_chunks.append(chunk)

        return final_chunks
    except Exception as e:
        logger.error(f"Reranking 과정에서 오류 발생: {e}")
        return chunks[:top_k]
