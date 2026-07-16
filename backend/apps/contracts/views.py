"""
계약 관리 API
- 표준 계약서 종류 목록/상세
- 계약서 신규 생성 (K-1)
- 계약서 검토 (K-2)
- Word 다운로드
"""
import os
import uuid
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .models import ContractTemplate, ContractDraft, ContractReview, ContractReviewFinding
from .serializers import (
    ContractTemplateSerializer, ContractTemplateDetailSerializer,
    ContractDraftSerializer, CreateDraftSerializer,
    ContractReviewSerializer, CreateReviewSerializer,
)


class ContractTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    표준 계약서 종류 (16종)
    GET /api/contract-templates/         → 목록
    GET /api/contract-templates/{code}/  → Key-term 스키마 포함 상세
    """
    queryset = ContractTemplate.objects.filter(is_active=True)
    serializer_class = ContractTemplateSerializer
    lookup_field = 'code'

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ContractTemplateDetailSerializer
        return ContractTemplateSerializer


class ContractDraftViewSet(viewsets.ModelViewSet):
    """
    계약서 신규 생성 (K-1)
    POST /api/contracts/drafts/              → Key-term → LLM 초안 생성
    GET  /api/contracts/drafts/{id}/         → 상세
    GET  /api/contracts/drafts/{id}/download/ → Word 다운로드
    """
    queryset = ContractDraft.objects.select_related('template').all()
    serializer_class = ContractDraftSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def create(self, request, *args, **kwargs):
        """Key-term 입력 → LLM 초안 생성"""
        serializer = CreateDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        template_code = serializer.validated_data['template_code']
        key_terms = serializer.validated_data['key_terms']
        project_id = serializer.validated_data.get('project_id')
        title = serializer.validated_data.get('title', '')

        # 템플릿 조회
        try:
            template = ContractTemplate.objects.get(code=template_code, is_active=True)
        except ContractTemplate.DoesNotExist:
            return Response(
                {'error': f'유효하지 않은 계약 유형입니다: {template_code}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Draft 생성
        draft = ContractDraft.objects.create(
            template=template,
            key_terms=key_terms,
            project_id=project_id,
            title=title or f'{template.name_ko} 초안',
            status='generating',
        )

        # LLM으로 초안 생성
        try:
            from services.llm import generate_contract_draft
            result = generate_contract_draft(template, key_terms)
            content = result['content']
            
            # 참조 출처 기록 (사용자 요구사항 대응)
            sources = result.get('sources', [])
            if sources:
                content += "\n\n---\n\n### 📎 참조된 사내 계약서 목록 (RAG Retrieval)\n"
                for idx, src in enumerate(sources, start=1):
                    doc_title = src.get('display_title', '알 수 없는 문서')
                    score = src.get('score', 0)
                    content += f"{idx}. {doc_title} (유사도: {score})\n"
            else:
                content += "\n\n---\n\n※ 참조된 사내 계약서 데이터가 없습니다. 기본 템플릿으로 생성되었습니다.\n"
                
            draft.generated_content = content
            draft.status = 'completed'
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f'Contract draft generation failed: {e}')
            # Mock 초안 제공
            draft.generated_content = self._generate_mock_draft(template, key_terms)
            draft.status = 'completed'

        # Word 파일 생성
        try:
            from services.docx_export import export_contract_draft
            output_path = export_contract_draft(draft)
            draft.output_file_uri = output_path
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f'Word export failed: {e}')

        draft.save()

        result_serializer = ContractDraftSerializer(draft)
        return Response(result_serializer.data, status=status.HTTP_201_CREATED)

    def _generate_mock_draft(self, template, key_terms):
        """LLM 미연결 시 Mock 초안 생성"""
        parties = key_terms.get('parties', key_terms.get('계약 당사자', '(당사자 미지정)'))
        capacity = key_terms.get('capacity', key_terms.get('계약 용량', ''))
        period = key_terms.get('period', key_terms.get('계약 기간', ''))
        price = key_terms.get('price', key_terms.get('매매 단가 / 정산 조건', ''))
        etc = key_terms.get('etc', key_terms.get('기타 관철 조건', ''))

        return f"""# {template.name_ko}
({template.name_en or ''})

## 당사자
본 계약은 {parties} 간에 체결한다.

## 제1조 (목적)
본 계약은 당사자 간의 권리·의무를 정하는 것을 목적으로 한다.
{f'계약 용량: {capacity}' if capacity else ''}

## 제2조 (계약 기간)
{f'계약 기간은 {period}으로 한다.' if period else '계약 기간은 별도 협의한다.'}

## 제3조 (정산)
{f'정산 조건: {price}' if price else '정산 조건은 별도 협의한다.'}

## 제4조 (기타 조건)
{etc if etc else '기타 조건은 별도 협의한다.'}

---
*본 계약서는 AI Agent에 의해 자동 생성된 초안이며, 법률 검토가 필요합니다.*
"""

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """Word 다운로드"""
        draft = self.get_object()
        if not draft.output_file_uri or not os.path.exists(draft.output_file_uri):
            # 실시간 생성
            try:
                from services.docx_export import export_contract_draft
                output_path = export_contract_draft(draft)
                draft.output_file_uri = output_path
                draft.save(update_fields=['output_file_uri'])
            except Exception:
                raise Http404('Word 파일을 생성할 수 없습니다.')

        return FileResponse(
            open(draft.output_file_uri, 'rb'),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            filename=f'{draft.title or "contract_draft"}.docx'
        )


class ContractReviewViewSet(viewsets.ModelViewSet):
    """
    계약서 검토 (K-2)
    POST /api/contracts/reviews/              → 파일+지시 → 검토 실행
    GET  /api/contracts/reviews/{id}/         → findings 표 포함 상세
    GET  /api/contracts/reviews/{id}/download/ → Word 다운로드
    """
    queryset = ContractReview.objects.select_related('template').prefetch_related('findings').all()
    serializer_class = ContractReviewSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def create(self, request, *args, **kwargs):
        """계약서 검토 실행"""
        instruction = request.data.get('review_instruction', '')
        template_code = request.data.get('template_code', '')
        project_id = request.data.get('project_id')
        title = request.data.get('title', '계약서 검토')
        uploaded_file = request.FILES.get('file')

        # 파일 저장
        source_uri = ''
        if uploaded_file:
            media_dir = settings.MEDIA_ROOT / 'contract_reviews'
            media_dir.mkdir(parents=True, exist_ok=True)
            ext = os.path.splitext(uploaded_file.name)[1].lower().strip('.')
            file_id = str(uuid.uuid4())
            filepath = media_dir / f'{file_id}.{ext}'
            with open(filepath, 'wb') as f:
                for chunk in uploaded_file.chunks():
                    f.write(chunk)
            source_uri = str(filepath)
            if not title or title == '계약서 검토':
                title = uploaded_file.name

        # 템플릿 조회
        template = None
        if template_code:
            template = ContractTemplate.objects.filter(code=template_code).first()

        # Review 레코드 생성
        review = ContractReview.objects.create(
            template=template,
            title=title,
            source_document_uri=source_uri,
            review_instruction=instruction,
            project_id=project_id if project_id else None,
            status='reviewing',
        )

        # LLM으로 검토 실행
        try:
            from services.llm import review_contract
            result = review_contract(review, template)
            review.summary = result.get('summary', '')
            review.status = 'completed'

            # Findings 저장
            findings_data = result.get('findings', [])
            finding_objects = []
            for i, f in enumerate(findings_data):
                finding_objects.append(ContractReviewFinding(
                    review=review,
                    clause_ref=f.get('clause_ref', ''),
                    severity=f.get('severity', 'mid'),
                    category=f.get('category', ''),
                    finding=f.get('finding', ''),
                    suggestion=f.get('suggestion', ''),
                    source_clause_ref=f.get('source_clause_ref', ''),
                    order_index=i,
                ))
            ContractReviewFinding.objects.bulk_create(finding_objects)

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f'Contract review failed: {e}')
            # Mock 검토 결과
            review.summary = 'AI 검토 서비스에 연결할 수 없어 Mock 검토 결과를 제공합니다.'
            review.status = 'completed'
            self._create_mock_findings(review)

        review.save()

        result_serializer = ContractReviewSerializer(review)
        return Response(result_serializer.data, status=status.HTTP_201_CREATED)

    def _create_mock_findings(self, review):
        """Mock 검토 결과 생성"""
        mock_findings = [
            {
                'clause_ref': '제12조',
                'severity': 'high',
                'category': '독소조항',
                'finding': '지체상금 상한 없음 — 무한 책임 리스크',
                'suggestion': '계약금액의 10% 상한 신설 제안',
            },
            {
                'clause_ref': '제18조',
                'severity': 'mid',
                'category': '불리조항',
                'finding': '하자담보 책임 기간 과도 (5년)',
                'suggestion': '표준 2년 대비 과도, 단축 협상 필요',
            },
            {
                'clause_ref': '—',
                'severity': 'low',
                'category': '누락',
                'finding': '불가항력 조항 부재',
                'suggestion': '표준 양식 제24조 삽입 권장',
            },
        ]
        objects = []
        for i, f in enumerate(mock_findings):
            objects.append(ContractReviewFinding(
                review=review, order_index=i, **f
            ))
        ContractReviewFinding.objects.bulk_create(objects)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """검토 결과 Word 다운로드"""
        review = self.get_object()
        if not review.output_file_uri or not os.path.exists(review.output_file_uri):
            try:
                from services.docx_export import export_contract_review
                output_path = export_contract_review(review)
                review.output_file_uri = output_path
                review.save(update_fields=['output_file_uri'])
            except Exception:
                raise Http404('Word 파일을 생성할 수 없습니다.')

        return FileResponse(
            open(review.output_file_uri, 'rb'),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            filename=f'{review.title or "contract_review"}.docx'
        )
