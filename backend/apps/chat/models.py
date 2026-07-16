"""
대화 모델 — RAG 챗봇 핵심
- Conversation: 대화 세션
- Message: 개별 메시지
- MessageSource: 출처 인용 (UI 칩/원문 위치)
- MessageAttachment: 대화 중 첨부 파일
- ConversationShare: 대화 공유/반출
"""
import uuid
from django.db import models
from django.conf import settings


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'workspaces.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='conversations'
    )
    title = models.CharField(max_length=300, blank=True, default='')
    use_internal_docs = models.BooleanField(
        default=True, help_text='사내 문서 참조 토글(대화별)'
    )
    is_shared = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='conversations'
    )
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'conversations'
        verbose_name = '대화'
        verbose_name_plural = '대화'
        ordering = ['-last_message_at', '-created_at']

    def __str__(self):
        return self.title or f'대화 {str(self.id)[:8]}'


class Message(models.Model):
    ROLE_CHOICES = [
        ('user', '사용자'),
        ('assistant', 'AI'),
        ('system', '시스템'),
    ]
    STATUS_CHOICES = [
        ('pending', '처리 중'),
        ('done', '완료'),
        ('failed', '실패'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='messages'
    )
    role = models.CharField(max_length=12, choices=ROLE_CHOICES)
    content = models.TextField()
    used_internal_docs = models.BooleanField(default=False)
    model = models.CharField(max_length=80, blank=True, default='')
    token_usage = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='done')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'messages'
        verbose_name = '메시지'
        verbose_name_plural = '메시지'
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.role}] {self.content[:50]}'


class MessageSource(models.Model):
    """출처(인용) — UI 칩/원문 위치 핵심"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name='sources'
    )
    document = models.ForeignKey(
        'documents.Document', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    document_chunk = models.ForeignKey(
        'documents.DocumentChunk', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    display_title = models.CharField(max_length=500, help_text='전체 파일명(호버 팝업)')
    short_label = models.CharField(max_length=20, blank=True, default='', help_text='표시용 5자(칩)')
    page_number = models.IntegerField(null=True, blank=True)
    location_label = models.CharField(max_length=120, blank=True, default='', help_text='예: p.12 · 제3조')
    score = models.FloatField(null=True, blank=True)
    rank = models.IntegerField(null=True, blank=True)
    snippet = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'message_sources'
        verbose_name = '메시지 출처'
        verbose_name_plural = '메시지 출처'
        ordering = ['rank']


class MessageAttachment(models.Model):
    """대화 중 첨부 파일"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='attachments'
    )
    message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True
    )
    filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=10, blank=True, default='')
    storage_uri = models.CharField(max_length=1000)
    file_size = models.BigIntegerField(null=True, blank=True)
    parsed_text_uri = models.CharField(max_length=1000, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'message_attachments'
        verbose_name = '첨부파일'
        verbose_name_plural = '첨부파일'


class ConversationShare(models.Model):
    """대화 공유/반출 → 공용 프로젝트 방"""
    SHARE_TYPE_CHOICES = [
        ('copy', '복사'),
        ('move', '이동'),
        ('link', '링크'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='shares'
    )
    shared_to_project = models.ForeignKey(
        'workspaces.Project', on_delete=models.CASCADE, related_name='shared_conversations'
    )
    share_type = models.CharField(max_length=10, choices=SHARE_TYPE_CHOICES, default='copy')
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'conversation_shares'
        verbose_name = '대화 공유'
        verbose_name_plural = '대화 공유'
