import hashlib
import os
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.http import FileResponse, Http404
from django_filters.rest_framework import DjangoFilterBackend

from .models import Document, DocumentChunk
from .serializers import DocumentSerializer, DocumentUploadSerializer, DocumentChunkSerializer


class DocumentViewSet(viewsets.ModelViewSet):
    """
    문서 관리 API
    - POST /api/documents/  → 업로드 (비동기 수집 트리거)
    - GET  /api/documents/  → 목록
    - GET  /api/documents/{id}/  → 상세
    - GET  /api/documents/{id}/file/  → 원문 열람
    - GET  /api/documents/{id}/status/  → 수집 진행 상태
    """
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project', 'status', 'file_type']
    parser_classes = [MultiPartParser, FormParser]

    def create(self, request, *args, **kwargs):
        """문서 업로드 → 저장 후 비동기 수집 트리거"""
        upload_serializer = DocumentUploadSerializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)

        uploaded_file = upload_serializer.validated_data['file']
        project_id = upload_serializer.validated_data.get('project_id')
        title = upload_serializer.validated_data.get('title', uploaded_file.name)

        # 파일 타입 판별
        ext = os.path.splitext(uploaded_file.name)[1].lower().strip('.')
        if ext not in ('pdf', 'docx', 'xlsx'):
            return Response(
                {'error': '지원되지 않는 파일 형식입니다. (PDF, Word, Excel만 가능)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 파일 저장
        media_dir = settings.MEDIA_ROOT / 'documents'
        media_dir.mkdir(parents=True, exist_ok=True)

        import uuid
        file_id = str(uuid.uuid4())
        filename = f'{file_id}.{ext}'
        filepath = media_dir / filename

        # 체크섬 계산 및 저장
        hasher = hashlib.sha256()
        with open(filepath, 'wb') as f:
            for chunk in uploaded_file.chunks():
                hasher.update(chunk)
                f.write(chunk)

        checksum = hasher.hexdigest()

        # 중복 체크
        existing = Document.objects.filter(checksum=checksum).first()
        if existing:
            os.remove(filepath)
            return Response(
                {'error': '이미 등록된 문서입니다.', 'document_id': str(existing.id)},
                status=status.HTTP_409_CONFLICT
            )

        # Document 레코드 생성
        document = Document.objects.create(
            title=title,
            original_filename=uploaded_file.name,
            file_type=ext,
            storage_uri=str(filepath),
            file_size=uploaded_file.size,
            checksum=checksum,
            project_id=project_id,
            status='uploaded',
        )

        # 비동기 수집 태스크 트리거 (Celery)
        try:
            from .tasks import process_document
            process_document.delay(str(document.id))
        except Exception:
            pass  # Celery 미실행 시 무시, 수동 처리 가능

        serializer = self.get_serializer(document)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def file(self, request, pk=None):
        """원문 파일 열람 (출처 클릭 시 사용)"""
        document = self.get_object()
        filepath = document.storage_uri

        if not os.path.exists(filepath):
            raise Http404('파일을 찾을 수 없습니다.')

        content_types = {
            'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }
        content_type = content_types.get(document.file_type, 'application/octet-stream')

        return FileResponse(
            open(filepath, 'rb'),
            content_type=content_type,
            as_attachment=False,
            filename=document.original_filename
        )

    @action(detail=True, methods=['get'])
    def status_check(self, request, pk=None):
        """수집 진행 상태 확인"""
        document = self.get_object()
        return Response({
            'id': str(document.id),
            'status': document.status,
            'chunk_count': document.chunks.count(),
            'indexed_at': document.indexed_at,
        })

    @action(detail=True, methods=['get'])
    def chunks(self, request, pk=None):
        """문서의 청크 목록 조회"""
        document = self.get_object()
        chunks = document.chunks.all().order_by('chunk_index')
        serializer = DocumentChunkSerializer(chunks, many=True)
        return Response(serializer.data)
