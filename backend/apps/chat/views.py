"""
RAG 챗봇 API
- 대화 CRUD
- 메시지 전송 (질의 → RAG 검색 → LLM 답변 → 출처 매핑)
- 첨부 파일 업로드
- 대화 공유/반출
"""
import os
import uuid
from django.conf import settings
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import Conversation, Message, MessageSource, MessageAttachment, ConversationShare
from .serializers import (
    ConversationSerializer, ConversationDetailSerializer,
    MessageSerializer, SendMessageSerializer,
    ShareConversationSerializer, MessageAttachmentSerializer
)


class ConversationViewSet(viewsets.ModelViewSet):
    """
    대화 관리 API
    - GET    /api/conversations/           → 목록
    - POST   /api/conversations/           → 새 대화 생성
    - GET    /api/conversations/{id}/      → 상세 (메시지 포함)
    - PATCH  /api/conversations/{id}/      → 수정 (title, use_internal_docs)
    - POST   /api/conversations/{id}/messages/     → 메시지 전송 (핵심!)
    - POST   /api/conversations/{id}/attachments/  → 파일 첨부
    - POST   /api/conversations/{id}/share/        → 공유/반출
    """
    queryset = Conversation.objects.all()
    serializer_class = ConversationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project', 'is_shared']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ConversationDetailSerializer
        return ConversationSerializer

    @action(detail=True, methods=['post'], url_path='messages')
    def send_message(self, request, pk=None):
        """
        메시지 전송 → RAG 검색 → LLM 답변 → 출처 매핑
        기획서 C-1~C-5 핵심 기능
        """
        conversation = self.get_object()
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_content = serializer.validated_data['content']
        use_docs = serializer.validated_data.get('use_internal_docs', conversation.use_internal_docs)

        # 1. 사용자 메시지 저장
        user_message = Message.objects.create(
            conversation=conversation,
            role='user',
            content=user_content,
        )

        # 2. RAG 검색 (사내 문서 참조가 활성화된 경우)
        sources_data = []
        context_text = ''

        if use_docs:
            try:
                from services.rag import search_documents
                search_results = search_documents(
                    query=user_content,
                    project_id=str(conversation.project_id) if conversation.project_id else None,
                    top_k=15
                )
                sources_data = search_results.get('sources', [])
                context_text = search_results.get('context', '')
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f'RAG search failed: {e}')

        is_stream = request.data.get('stream', False)

        # 3. LLM 답변 생성 (대화 히스토리 구성)
        history = list(
            conversation.messages
            .filter(role__in=['user', 'assistant'])
            .order_by('created_at')
            .values('role', 'content')[:20]
        )

        if is_stream:
            from django.http import StreamingHttpResponse
            from services.llm import generate_answer_stream
            import json

            def event_stream():
                content_full = ""
                # 프론트엔드에 먼저 출처(Sources) 데이터를 보내줄 수 있습니다 (선택)
                yield json.dumps({"type": "sources", "sources": sources_data}) + "\n"

                try:
                    for chunk in generate_answer_stream(
                        question=user_content,
                        context=context_text,
                        history=history,
                        use_internal_docs=use_docs,
                    ):
                        try:
                            parsed_chunk = json.loads(chunk)
                            content_full += parsed_chunk.get("content", "")
                        except:
                            pass
                        yield chunk
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f'LLM stream failed: {e}')
                    yield json.dumps({"content": "\n[서버 에러가 발생했습니다.]"}) + "\n"

                # 스트리밍 완료 후 DB 저장
                ai_message = Message.objects.create(
                    conversation=conversation,
                    role='assistant',
                    content=content_full,
                    used_internal_docs=bool(sources_data),
                    status='done',
                )
                source_objects = []
                for i, src in enumerate(sources_data):
                    source_objects.append(MessageSource(
                        message=ai_message,
                        document_id=src.get('document_id'),
                        document_chunk_id=src.get('chunk_id'),
                        display_title=src.get('display_title', ''),
                        short_label=src.get('short_label', ''),
                        page_number=src.get('page_number'),
                        location_label=src.get('location_label', ''),
                        score=src.get('score'),
                        rank=i + 1,
                        snippet=src.get('snippet', ''),
                    ))
                if source_objects:
                    MessageSource.objects.bulk_create(source_objects)
                
                conversation.last_message_at = timezone.now()
                if not conversation.title:
                    conversation.title = user_content[:50]
                conversation.save(update_fields=['last_message_at', 'title', 'updated_at'])

            return StreamingHttpResponse(event_stream(), content_type='text/event-stream')

        else:
            try:
                from services.llm import generate_answer
                answer = generate_answer(
                    question=user_content,
                    context=context_text,
                    history=history,
                    use_internal_docs=use_docs,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f'LLM generation failed: {e}')
                answer = {
                    'content': '죄송합니다. 현재 AI 응답을 생성할 수 없습니다. 잠시 후 다시 시도해주세요.',
                    'model': 'error',
                    'token_usage': None,
                }

            # 4. AI 응답 메시지 저장
            ai_message = Message.objects.create(
                conversation=conversation,
                role='assistant',
                content=answer['content'],
                used_internal_docs=bool(sources_data),
                model=answer.get('model', ''),
                token_usage=answer.get('token_usage'),
                status='done',
            )

            # 5. 출처 저장
            source_objects = []
            for i, src in enumerate(sources_data):
                source_objects.append(MessageSource(
                    message=ai_message,
                    document_id=src.get('document_id'),
                    document_chunk_id=src.get('chunk_id'),
                    display_title=src.get('display_title', ''),
                    short_label=src.get('short_label', ''),
                    page_number=src.get('page_number'),
                    location_label=src.get('location_label', ''),
                    score=src.get('score'),
                    rank=i + 1,
                    snippet=src.get('snippet', ''),
                ))
            if source_objects:
                MessageSource.objects.bulk_create(source_objects)

            # 6. 대화 타임스탬프 업데이트
            conversation.last_message_at = timezone.now()
            if not conversation.title:
                conversation.title = user_content[:50]
            conversation.save(update_fields=['last_message_at', 'title', 'updated_at'])

            # 7. 응답 반환
            ai_msg_serializer = MessageSerializer(ai_message)
            return Response(ai_msg_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='attachments',
            parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request, pk=None):
        """대화 중 파일 첨부 (C-6)"""
        conversation = self.get_object()
        uploaded_file = request.FILES.get('file')

        if not uploaded_file:
            return Response({'error': '파일이 필요합니다.'}, status=status.HTTP_400_BAD_REQUEST)

        # 파일 저장
        media_dir = settings.MEDIA_ROOT / 'attachments'
        media_dir.mkdir(parents=True, exist_ok=True)

        ext = os.path.splitext(uploaded_file.name)[1].lower().strip('.')
        file_id = str(uuid.uuid4())
        filename = f'{file_id}.{ext}'
        filepath = media_dir / filename

        with open(filepath, 'wb') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)

        attachment = MessageAttachment.objects.create(
            conversation=conversation,
            filename=uploaded_file.name,
            file_type=ext,
            storage_uri=str(filepath),
            file_size=uploaded_file.size,
        )

        serializer = MessageAttachmentSerializer(attachment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='share')
    def share_conversation(self, request, pk=None):
        """대화 공유/반출 → 공용 프로젝트 방 (C-7)"""
        conversation = self.get_object()
        serializer = ShareConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        share = ConversationShare.objects.create(
            conversation=conversation,
            shared_to_project_id=serializer.validated_data['project_id'],
            share_type=serializer.validated_data['share_type'],
        )

        conversation.is_shared = True
        conversation.save(update_fields=['is_shared', 'updated_at'])

        return Response({
            'id': str(share.id),
            'conversation_id': str(conversation.id),
            'shared_to_project_id': str(share.shared_to_project_id),
            'share_type': share.share_type,
        }, status=status.HTTP_201_CREATED)
