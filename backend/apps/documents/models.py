"""
문서 · 청크 모델
- Document: 업로드된 사내 자료 원본 메타데이터
- DocumentChunk: 청크 메타(벡터 본체는 Qdrant에 저장)
"""
import uuid
from django.db import models
from django.conf import settings


class Document(models.Model):
    STATUS_CHOICES = [
        ('uploaded', '업로드 완료'),
        ('parsing', '파싱 중'),
        ('embedding', '임베딩 중'),
        ('indexed', '인덱싱 완료'),
        ('failed', '실패'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'workspaces.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='documents',
        help_text='NULL이면 전사 공용 코퍼스'
    )
    title = models.CharField(max_length=500)
    original_filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=10, help_text='pdf | docx | xlsx')
    storage_uri = models.CharField(max_length=1000)
    file_size = models.BigIntegerField(null=True, blank=True)
    page_count = models.IntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    metadata = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    indexed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'documents'
        verbose_name = '문서'
        verbose_name_plural = '문서'
        indexes = [
            models.Index(fields=['project'], name='idx_documents_project_dj'),
            models.Index(fields=['status'], name='idx_documents_status_dj'),
        ]

    def __str__(self):
        return self.title


class DocumentChunk(models.Model):
    CHUNK_ROLE_CHOICES = [
        ('parent', 'Parent (답변용 전체 조항)'),
        ('child', 'Child (검색용 세부 문장)'),
        ('standalone', 'Standalone (단독 청크)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name='chunks'
    )
    chunk_index = models.IntegerField()
    content = models.TextField()
    page_number = models.IntegerField(null=True, blank=True)
    section_title = models.CharField(max_length=300, blank=True, default='')
    char_start = models.IntegerField(null=True, blank=True)
    char_end = models.IntegerField(null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True)
    sheet_name = models.CharField(max_length=200, blank=True, default='')
    cell_range = models.CharField(max_length=100, blank=True, default='')
    token_count = models.IntegerField(null=True, blank=True)
    qdrant_point_id = models.UUIDField(unique=True)
    metadata = models.JSONField(default=dict, blank=True,
                                help_text='article_number, article_title, chunk_type 등')

    # ── Parent-Child 구조 ──────────────────────────────────────────
    chunk_role = models.CharField(
        max_length=20, choices=CHUNK_ROLE_CHOICES, default='standalone',
        help_text='parent: 전체 조항(LLM용) | child: 세부 문장(검색용) | standalone: 단독'
    )
    parent_chunk = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='children',
        help_text='child 청크의 경우 부모 parent 청크 참조'
    )

    # ── 경로 기반 메타데이터 ──────────────────────────────────────
    project_name = models.CharField(max_length=200, blank=True, default='',
                                    help_text='예: 당진 1단계, 태평, 홍성')
    spc_name = models.CharField(max_length=200, blank=True, default='',
                                help_text='예: 당진행복솔라, 신안증도태양광')
    document_type = models.CharField(max_length=50, blank=True, default='general',
                                     help_text='contract | report | financial | general')
    sub_type = models.CharField(max_length=100, blank=True, default='',
                                help_text='예: EPC, O&M, PPA, PF')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'document_chunks'
        verbose_name = '문서 청크'
        verbose_name_plural = '문서 청크'
        indexes = [
            models.Index(fields=['document'], name='idx_chunks_document_dj'),
            models.Index(fields=['chunk_role'], name='idx_chunks_role'),
            models.Index(fields=['project_name'], name='idx_chunks_project'),
            models.Index(fields=['document_type'], name='idx_chunks_doctype'),
        ]

    def __str__(self):
        return f'{self.document.title} - chunk #{self.chunk_index} [{self.chunk_role}]'

