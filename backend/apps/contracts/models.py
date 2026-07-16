"""
계약 모델
- ContractTemplate: 표준 계약서 종류(마스터)
- ContractDraft: 계약서 신규 생성 (K-1)
- ContractReview: 계약서 검토 (K-2)
- ContractReviewFinding: 검토 결과 항목
"""
import uuid
from django.db import models
from django.conf import settings


class ContractTemplate(models.Model):
    """표준 계약서 종류 — 16종 마스터 데이터"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True, help_text='JDA, SPA, SHA, EPC ...')
    name_ko = models.CharField(max_length=150)
    name_en = models.CharField(max_length=200, blank=True, default='')
    category = models.CharField(max_length=50, blank=True, default='')
    description = models.TextField(blank=True, default='')
    template_body = models.TextField(blank=True, default='', help_text='표준 양식(placeholder)')
    key_term_schema = models.JSONField(
        default=list, blank=True,
        help_text='입력 필드 정의(라벨/타입/필수)'
    )
    standard_clauses = models.JSONField(default=list, blank=True)
    review_checklist = models.JSONField(default=list, blank=True)
    version = models.CharField(max_length=20, default='v1')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contract_templates'
        verbose_name = '표준 계약서'
        verbose_name_plural = '표준 계약서'

    def __str__(self):
        return f'[{self.code}] {self.name_ko}'


class ContractDraft(models.Model):
    """계약서 신규 생성 (K-1)"""
    STATUS_CHOICES = [
        ('draft', '초안'),
        ('generating', '생성 중'),
        ('completed', '완료'),
        ('failed', '실패'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'workspaces.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contract_drafts'
    )
    template = models.ForeignKey(
        ContractTemplate, on_delete=models.PROTECT, related_name='drafts'
    )
    title = models.CharField(max_length=300, blank=True, default='')
    key_terms = models.JSONField(default=dict, blank=True, help_text='사용자 입력 Key-term')
    generated_content = models.TextField(blank=True, default='')
    output_file_uri = models.CharField(max_length=1000, blank=True, default='')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='draft')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contract_drafts'
        verbose_name = '계약서 초안'
        verbose_name_plural = '계약서 초안'

    def __str__(self):
        return self.title or f'{self.template.name_ko} 초안'


class ContractReview(models.Model):
    """계약서 검토 (K-2)"""
    STATUS_CHOICES = [
        ('pending', '대기'),
        ('reviewing', '검토 중'),
        ('completed', '완료'),
        ('failed', '실패'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'workspaces.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contract_reviews'
    )
    template = models.ForeignKey(
        ContractTemplate, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviews',
        help_text='추정 유형'
    )
    title = models.CharField(max_length=300, blank=True, default='')
    source_document_uri = models.CharField(max_length=1000, blank=True, default='')
    review_instruction = models.TextField(blank=True, default='')
    summary = models.TextField(blank=True, default='')
    output_file_uri = models.CharField(max_length=1000, blank=True, default='')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contract_reviews'
        verbose_name = '계약서 검토'
        verbose_name_plural = '계약서 검토'

    def __str__(self):
        return self.title or '계약서 검토'


class ContractReviewFinding(models.Model):
    """검토 결과 항목"""
    SEVERITY_CHOICES = [
        ('high', '독소'),
        ('mid', '불리'),
        ('low', '경고/누락'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(
        ContractReview, on_delete=models.CASCADE, related_name='findings'
    )
    clause_ref = models.CharField(max_length=100, blank=True, default='')
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='mid')
    category = models.CharField(max_length=30, blank=True, default='')
    finding = models.TextField()
    suggestion = models.TextField(blank=True, default='')
    source_clause_ref = models.CharField(max_length=100, blank=True, default='')
    order_index = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'contract_review_findings'
        verbose_name = '검토 결과'
        verbose_name_plural = '검토 결과'
        ordering = ['order_index']

    def __str__(self):
        return f'{self.clause_ref}: {self.finding[:50]}'
