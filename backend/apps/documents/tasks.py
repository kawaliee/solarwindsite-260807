"""
문서 수집 Celery 태스크
업로드 → 파싱 → 청킹 → (임베딩 → Qdrant 적재)
"""
import uuid
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_document(self, document_id: str):
    """
    문서 수집 파이프라인:
    1. 파싱 (PDF/Word/Excel → 텍스트 추출)
    2. 청킹 (텍스트 분할)
    3. 임베딩 (BGE-M3)
    4. Qdrant 적재
    """
    from .models import Document, DocumentChunk

    try:
        document = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error(f'Document {document_id} not found')
        return

    try:
        # Step 1: 파싱
        document.status = 'parsing'
        document.save(update_fields=['status'])

        from services.parser import parse_document
        parsed = parse_document(document.storage_uri, document.file_type)

        document.page_count = parsed.get('page_count')

        # Step 2: 청킹
        chunks_data = parsed.get('chunks', [])

        # Step 3: 임베딩
        document.status = 'embedding'
        document.save(update_fields=['status'])

        from services.embedding import get_embeddings
        texts = [c['content'] for c in chunks_data]

        try:
            embeddings = get_embeddings(texts)
        except Exception as e:
            logger.warning(f'Embedding failed, using empty: {e}')
            embeddings = [None] * len(texts)

        # Step 4: DB 저장 + Qdrant 적재
        chunk_objects = []
        for i, chunk_data in enumerate(chunks_data):
            point_id = uuid.uuid4()
            chunk_objects.append(DocumentChunk(
                document=document,
                chunk_index=i,
                content=chunk_data['content'],
                page_number=chunk_data.get('page_number'),
                section_title=chunk_data.get('section_title', ''),
                char_start=chunk_data.get('char_start'),
                char_end=chunk_data.get('char_end'),
                token_count=len(chunk_data['content'].split()),
                qdrant_point_id=point_id,
            ))

        DocumentChunk.objects.bulk_create(chunk_objects)

        # Qdrant 적재 시도
        try:
            from services.qdrant_service import upsert_chunks
            upsert_chunks(document, chunk_objects, embeddings)
        except Exception as e:
            logger.warning(f'Qdrant upsert failed: {e}')

        # 완료
        from django.utils import timezone
        document.status = 'indexed'
        document.indexed_at = timezone.now()
        document.save(update_fields=['status', 'indexed_at', 'page_count'])

        logger.info(f'Document {document_id} indexed: {len(chunk_objects)} chunks')

    except Exception as exc:
        document.status = 'failed'
        document.save(update_fields=['status'])
        logger.error(f'Document {document_id} processing failed: {exc}')
        raise self.retry(exc=exc, countdown=60)
