from rest_framework import serializers
from .models import Conversation, Message, MessageSource, MessageAttachment, ConversationShare


class MessageSourceSerializer(serializers.ModelSerializer):
    open_url = serializers.SerializerMethodField()

    class Meta:
        model = MessageSource
        fields = ['id', 'document_id', 'document_chunk_id', 'display_title', 'short_label',
                  'page_number', 'location_label', 'score', 'rank',
                  'snippet', 'open_url']

    def get_open_url(self, obj):
        if obj.document_id:
            url = f'/api/documents/{obj.document_id}/file/'
            if obj.page_number:
                url += f'?page={obj.page_number}'
            return url
        return None


class MessageSerializer(serializers.ModelSerializer):
    sources = MessageSourceSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'conversation', 'role', 'content', 'used_internal_docs',
                  'model', 'token_usage', 'status', 'created_at', 'sources']
        read_only_fields = ['id', 'created_at', 'status', 'sources']


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'project', 'title', 'use_internal_docs', 'is_shared',
                  'created_by', 'last_message_at', 'created_at', 'updated_at',
                  'message_count', 'last_message_preview']
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_message_at']

    def get_message_count(self, obj):
        return obj.messages.count()

    def get_last_message_preview(self, obj):
        last_msg = obj.messages.order_by('-created_at').first()
        if last_msg:
            return last_msg.content[:100]
        return None


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ['messages']


class SendMessageSerializer(serializers.Serializer):
    """메시지 전송 요청용"""
    content = serializers.CharField()
    use_internal_docs = serializers.BooleanField(default=True)


class ShareConversationSerializer(serializers.Serializer):
    """대화 공유 요청용"""
    project_id = serializers.UUIDField()
    share_type = serializers.ChoiceField(choices=['copy', 'move', 'link'], default='copy')


class MessageAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageAttachment
        fields = ['id', 'filename', 'file_type', 'file_size', 'created_at']
        read_only_fields = ['id', 'created_at']
