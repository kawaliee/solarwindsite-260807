"""사업 Fact-sheet API"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status as http
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from . import schema as fs_schema
from .markdown import build_markdown, default_filename
from .models import ProjectFactSheet
from .serializers import FactSheetListSerializer, FactSheetSerializer

logger = logging.getLogger(__name__)

# RAG 적재 시 document_type 이 'report' 로 잡히도록 하는 중간 폴더.
# metadata_extractor 는 media/{PJT폴더}/{대분류}/{파일} 구조에서 대분류를 읽는다.
PUBLISH_SUBDIR = '사업개요'


@api_view(['GET'])
def factsheet_schema(request):
    """입력 폼 · 마크다운 생성기가 공유하는 항목 정의를 그대로 내려보낸다."""
    return Response(fs_schema.as_dict())


class FactSheetViewSet(viewsets.ModelViewSet):
    """사업 Fact-sheet CRUD"""
    queryset = ProjectFactSheet.objects.select_related('project').all()

    def get_serializer_class(self):
        return FactSheetListSerializer if self.action == 'list' else FactSheetSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        stage = self.request.query_params.get('stage')
        if stage:
            qs = qs.filter(stage=stage)
        q = (self.request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(name__icontains=q) | qs.filter(spc_name__icontains=q)
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        ser = self.get_serializer(qs, many=True)
        return Response({'count': qs.count(), 'results': ser.data})

    # ── 마크다운 미리보기 / 다운로드 ────────────────────────────
    @action(detail=True, methods=['get'])
    def markdown(self, request, pk=None):
        """
        ?download=1 이면 .md 파일로 내려받고, 아니면 본문을 JSON 으로 돌려준다.
        """
        sheet = self.get_object()
        md = build_markdown(sheet)
        filename = default_filename(sheet)

        if request.query_params.get('download'):
            resp = HttpResponse(md, content_type='text/markdown; charset=utf-8')
            resp['Content-Disposition'] = (
                f"attachment; filename*=UTF-8''{quote(filename)}"
            )
            return resp

        return Response({'filename': filename, 'markdown': md})

    # ── RAG 코퍼스로 발행 ───────────────────────────────────────
    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """
        마크다운을 media 폴더에 기록해 RAG 인덱싱 대상으로 만든다.

        경로: media/{pjt_folder}/사업개요/[사업개요] {SPC명}.md

        파일을 쓰기만 하며 임베딩은 하지 않는다. 적재는 아래 명령으로 수행한다.
            docker compose exec backend python manage.py ingest_media --dir "{pjt_folder}"
        """
        sheet = self.get_object()

        folder = (request.data or {}).get('pjt_folder') or sheet.pjt_folder or sheet.name
        folder = _safe_folder_name(folder)
        if not folder:
            return Response({'detail': 'RAG 폴더명이 비어 있습니다.'},
                            status=http.HTTP_400_BAD_REQUEST)

        media_root = str(settings.MEDIA_ROOT)
        target_dir = os.path.join(media_root, folder, PUBLISH_SUBDIR)

        # media 밖으로 벗어나는 경로를 원천 차단한다
        if not os.path.abspath(target_dir).startswith(os.path.abspath(media_root) + os.sep):
            return Response({'detail': '허용되지 않는 경로입니다.'},
                            status=http.HTTP_400_BAD_REQUEST)

        md = build_markdown(sheet)
        filename = default_filename(sheet)
        target_path = os.path.join(target_dir, filename)

        try:
            os.makedirs(target_dir, exist_ok=True)
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(md)
        except OSError as e:
            logger.exception('fact-sheet 발행 실패: %s', e)
            return Response({'detail': f'파일 기록에 실패했습니다: {e}'},
                            status=http.HTTP_500_INTERNAL_SERVER_ERROR)

        sheet.markdown = md
        sheet.pjt_folder = folder
        sheet.published_path = os.path.relpath(target_path, media_root)
        sheet.published_at = timezone.now()
        sheet.save(update_fields=['markdown', 'pjt_folder', 'published_path',
                                  'published_at', 'updated_at'])

        return Response({
            'published_path': sheet.published_path,
            'published_at': sheet.published_at,
            'bytes': len(md.encode('utf-8')),
            'ingest_command': (
                f'python manage.py ingest_media --dir "{folder}"'
            ),
            'note': '파일 기록까지 완료했습니다. 위 명령을 실행해야 검색 색인에 반영됩니다.',
        })


def _safe_folder_name(raw: str) -> str:
    """경로 구분자·상위 이동을 제거해 media 하위 단일 폴더명으로 만든다."""
    name = (raw or '').strip().strip('.')
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, '_')
    return name.strip()
