"""
BGE-M3 임베딩 및 Sparse(BM25) 임베딩 서비스
- API 모드: OpenRouter API를 통한 임베딩 생성
- 로컬 모드: SentenceTransformer 로컬 실행 (폴백)
- Sparse 모드: fastembed BM25 로컬 실행
"""
import logging
import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_local_model = None
_sparse_model = None


def get_embeddings(texts: list[str]) -> list:
    """
    텍스트 리스트 → 임베딩 벡터 리스트
    API 모드 사용 시 에러/속도제한으로 None이 반환되면 자동으로 로컬 임베딩으로 폴백 및 결합합니다.
    """
    if not texts:
        return []

    embeddings = []
    if settings.EMBEDDING_DEVICE == 'api':
        embeddings = _get_embeddings_api(texts)

    # API 결과가 비었거나 일부가 None인 경우 로컬 임베딩으로 보완
    if not embeddings or any(e is None for e in embeddings):
        logger.warning("일부 임베딩 생성 실패로 로컬 임베딩 폴백을 실행합니다.")
        local_embeddings = _get_embeddings_local(texts)
        if not embeddings:
            return local_embeddings
            
        # None인 항목만 로컬 임베딩 결과로 교체
        for idx in range(len(embeddings)):
            if embeddings[idx] is None and idx < len(local_embeddings):
                embeddings[idx] = local_embeddings[idx]

    return embeddings


def get_single_embedding(text: str):
    """단일 텍스트 임베딩"""
    results = get_embeddings([text])
    return results[0] if results else None


def _get_embeddings_api(texts: list[str]) -> list:
    """OpenRouter API를 통한 임베딩 생성"""
    api_base = settings.EMBEDDING_API_BASE
    api_key = settings.EMBEDDING_API_KEY

    if not api_base or not api_key:
        logger.warning('Embedding API not configured, falling back to local')
        return _get_embeddings_local(texts)

    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        all_embeddings = []
        batch_size = 8
        import time

        for i in range(0, len(texts), batch_size):
            batch = [t[:8000] for t in texts[i:i + batch_size]]
            
            response = None
            for retry in range(3):
                try:
                    response = httpx.post(
                        f'{api_base.rstrip("/")}/embeddings',
                        headers=headers,
                        json={'model': settings.EMBEDDING_MODEL, 'input': batch},
                        timeout=120.0,
                    )
                    if response.status_code == 200:
                        break
                    else:
                        logger.warning(f"Embedding API error {response.status_code} (attempt {retry+1}/3), retrying in 2s...")
                        time.sleep(2.0)
                except Exception as ex:
                    logger.warning(f"Embedding API request exception (attempt {retry+1}/3): {ex}, retrying in 2s...")
                    time.sleep(2.0)

            if response and response.status_code == 200:
                data = response.json()
                try:
                    batch_embeddings = [item['embedding'] for item in data['data']]
                    all_embeddings.extend(batch_embeddings)
                except KeyError as ke:
                    logger.error(f"Embedding API response structure error (missing 'data'): {data}")
                    all_embeddings.extend([None] * len(batch))
            else:
                err_text = response.text[:300] if response else "No response"
                logger.error(f'Embedding API failed after 3 retries: {err_text}')
                all_embeddings.extend([None] * len(batch))

        return all_embeddings
    except Exception as e:
        logger.error(f'Embedding API request failed: {e}')
        return [None] * len(texts)


def _get_embeddings_local(texts: list[str]) -> list:
    """SentenceTransformer 로컬 임베딩 (폴백)"""
    global _local_model
    try:
        if _local_model is None:
            from sentence_transformers import SentenceTransformer
            _local_model = SentenceTransformer(
                settings.EMBEDDING_MODEL,
                device=settings.EMBEDDING_DEVICE if settings.EMBEDDING_DEVICE != 'api' else 'cpu',
            )
            logger.info(f'Local embedding model loaded: {settings.EMBEDDING_MODEL}')

        embeddings = _local_model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()
    except Exception as e:
        logger.error(f'Local embedding failed: {e}')
        return [None] * len(texts)


def get_sparse_embeddings(texts: list[str]) -> list:
    """
    FastEmbed를 활용한 희소 벡터(Sparse Vector, BM25) 생성
    반환값: [{'indices': [int], 'values': [float]}, ...]
    """
    global _sparse_model
    if not texts:
        return []

    try:
        if _sparse_model is None:
            from fastembed import SparseTextEmbedding
            _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
            logger.info('Local sparse model (Qdrant/bm25) loaded')

        embeddings = list(_sparse_model.embed(texts))
        
        # fastembed 반환값을 Qdrant SparseVectorStruct 포맷에 맞게 변환
        results = []
        for emb in embeddings:
            results.append({
                'indices': emb.indices.tolist() if hasattr(emb.indices, 'tolist') else list(emb.indices),
                'values': emb.values.tolist() if hasattr(emb.values, 'tolist') else list(emb.values)
            })
        return results
    except Exception as e:
        logger.error(f'Sparse embedding failed: {e}')
        return [None] * len(texts)

def get_single_sparse_embedding(text: str):
    """단일 텍스트 희소 임베딩"""
    results = get_sparse_embeddings([text])
    return results[0] if results else None
