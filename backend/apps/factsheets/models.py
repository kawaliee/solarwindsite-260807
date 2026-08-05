"""
사업 Fact-sheet 모델

사업(SPC) 1개 = 레코드 1개 원칙을 지킨다.
폴더 단위로 묶으면 사업 간 수치가 섞이기 때문이다
(실측: 당진PJT 폴더 안에 당진행복솔라·당진대호솔라 2개 사업이 공존).

입력값 본체는 `data` JSON 한 칼럼에 담고, 검색·목록에 필요한 값만
별도 칼럼으로 승격했다. 항목 정의는 schema.py 가 단독으로 관리한다.
"""
import uuid

from django.db import models
from django.conf import settings as django_settings


class ProjectFactSheet(models.Model):
    STAGE_CHOICES = [
        ('dev', '개발'),
        ('build', '건설'),
        ('ops', '운영'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'workspaces.Project', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='fact_sheets',
        help_text='연결된 프로젝트 (선택)'
    )

    # ── 식별 정보 ────────────────────────────────────────────────
    name = models.CharField(max_length=200, help_text='대표 사업명 (예: 당진행복솔라)')
    aliases = models.JSONField(
        default=list, blank=True,
        help_text='별칭 목록 — 약칭·구명칭·PJT명. 예: ["대호1차", "홍성 염해태양광"]'
    )
    spc_name = models.CharField(max_length=200, blank=True, default='', help_text='SPC 법인명')

    # ── 문서 성격 ────────────────────────────────────────────────
    stage = models.CharField(max_length=10, choices=STAGE_CHOICES, default='dev',
                             help_text='현재 사업 단계 (화면 강조 기준)')
    as_of_date = models.DateField(null=True, blank=True,
                                  help_text='기준일 — 낡은 개요는 확신에 찬 오답이 된다')
    author = models.CharField(max_length=100, blank=True, default='', help_text='작성자')

    # ── 입력값 본체 ──────────────────────────────────────────────
    data = models.JSONField(
        default=dict, blank=True,
        help_text='{ "<section_id>": { "<field_key>": {"v": 값, "s": 확정도} } }'
    )
    schema_version = models.CharField(max_length=20, default='v1')

    # ── RAG 발행 이력 ────────────────────────────────────────────
    pjt_folder = models.CharField(
        max_length=300, blank=True, default='',
        help_text='RAG 적재 대상 media 하위 PJT 폴더명 (예: 당진PJT(당진행복솔라)_1단계)'
    )
    markdown = models.TextField(blank=True, default='', help_text='최근 발행한 마크다운 스냅샷')
    published_path = models.CharField(max_length=1000, blank=True, default='')
    published_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='created_fact_sheets'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'project_fact_sheets'
        verbose_name = '사업 Fact-sheet'
        verbose_name_plural = '사업 Fact-sheet'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['project'], name='idx_factsheet_project'),
            models.Index(fields=['stage'], name='idx_factsheet_stage'),
        ]

    def __str__(self):
        return f'[사업개요] {self.name}'

    # ── 파생값 ───────────────────────────────────────────────────
    @property
    def all_names(self):
        """대표 사업명 + SPC명 + 별칭 (중복 제거, 입력 순서 유지)"""
        out = []
        for n in [self.name, self.spc_name, *(self.aliases or [])]:
            n = (n or '').strip()
            if n and n not in out:
                out.append(n)
        return out

    def value(self, section_id, field_key):
        """data 에서 스칼라 값만 꺼낸다 (없으면 '')"""
        cell = (self.data or {}).get(section_id, {}).get(field_key)
        if not isinstance(cell, dict):
            return ''
        v = cell.get('v')
        return '' if v is None else v

    def completeness(self):
        """⭐ 핵심 항목 충족률 — 작성 우선순위 안내에 쓴다."""
        from .schema import row_has_input, star_fields
        fields = star_fields()
        if not fields:
            return {'filled': 0, 'total': 0, 'ratio': 0.0}
        filled = 0
        for sec_id, field in fields:
            v = self.value(sec_id, field['key'])
            if isinstance(v, list):
                if any(row_has_input(row, field) for row in v):
                    filled += 1
            elif str(v).strip():
                filled += 1
        return {'filled': filled, 'total': len(fields), 'ratio': round(filled / len(fields), 3)}
