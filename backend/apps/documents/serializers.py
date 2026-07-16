from rest_framework import serializers
from .models import Document, DocumentChunk


class DocumentChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentChunk
        fields = ['id', 'chunk_index', 'content', 'page_number', 'section_title',
                  'sheet_name', 'cell_range', 'token_count', 'created_at']
        read_only_fields = ['id', 'created_at']


class DocumentSerializer(serializers.ModelSerializer):
    chunk_count = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ['id', 'project', 'title', 'original_filename', 'file_type',
                  'storage_uri', 'file_size', 'page_count', 'status', 'metadata',
                  'uploaded_by', 'created_at', 'indexed_at', 'chunk_count']
        read_only_fields = ['id', 'created_at', 'indexed_at', 'status', 'storage_uri']

    def get_chunk_count(self, obj):
        return obj.chunks.count()


class DocumentUploadSerializer(serializers.Serializer):
    """문서 업로드 요청용"""
    file = serializers.FileField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, max_length=500)
