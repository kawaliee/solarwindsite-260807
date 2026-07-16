"""
워크스페이스 · 프로젝트 모델
- Workspace: 최상위 컨테이너 (예: 사업개발실)
- Project: 개별 PJT 또는 주제 단위 (예: "새만금 태양광")
"""
import uuid
from django.db import models
from django.conf import settings as django_settings


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True)
    description = models.TextField(blank=True, default='')
    settings = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_workspaces'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workspaces'
        verbose_name = '워크스페이스'
        verbose_name_plural = '워크스페이스'

    def __str__(self):
        return self.name


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name='projects'
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    is_shared = models.BooleanField(default=False, help_text='공용 프로젝트(반출 목적지)')
    icon = models.CharField(max_length=50, blank=True, default='')
    color = models.CharField(max_length=20, blank=True, default='')
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_projects'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'projects'
        verbose_name = '프로젝트'
        verbose_name_plural = '프로젝트'
        indexes = [
            models.Index(fields=['workspace'], name='idx_projects_workspace_dj'),
        ]

    def __str__(self):
        return self.name
