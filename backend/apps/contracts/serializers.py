from rest_framework import serializers
from .models import ContractTemplate, ContractDraft, ContractReview, ContractReviewFinding


class ContractTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractTemplate
        fields = ['id', 'code', 'name_ko', 'name_en', 'category', 'description',
                  'key_term_schema', 'version', 'is_active']


class ContractTemplateDetailSerializer(ContractTemplateSerializer):
    class Meta(ContractTemplateSerializer.Meta):
        fields = ContractTemplateSerializer.Meta.fields + [
            'template_body', 'standard_clauses', 'review_checklist'
        ]


class ContractDraftSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name_ko', read_only=True)
    template_code = serializers.CharField(source='template.code', read_only=True)

    class Meta:
        model = ContractDraft
        fields = ['id', 'project', 'template', 'template_name', 'template_code',
                  'title', 'key_terms', 'generated_content', 'output_file_uri',
                  'status', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'generated_content',
                            'output_file_uri', 'status']


class CreateDraftSerializer(serializers.Serializer):
    """계약서 생성 요청"""
    template_code = serializers.CharField(max_length=20)
    key_terms = serializers.DictField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, max_length=300)


class ContractReviewFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractReviewFinding
        fields = ['id', 'clause_ref', 'severity', 'category', 'finding',
                  'suggestion', 'source_clause_ref', 'order_index']


class ContractReviewSerializer(serializers.ModelSerializer):
    findings = ContractReviewFindingSerializer(many=True, read_only=True)
    template_name = serializers.CharField(source='template.name_ko', read_only=True, default='')

    class Meta:
        model = ContractReview
        fields = ['id', 'project', 'template', 'template_name', 'title',
                  'source_document_uri', 'review_instruction', 'summary',
                  'output_file_uri', 'status', 'created_by', 'created_at',
                  'updated_at', 'findings']
        read_only_fields = ['id', 'created_at', 'updated_at', 'summary',
                            'output_file_uri', 'status', 'findings']


class CreateReviewSerializer(serializers.Serializer):
    """계약서 검토 요청"""
    file = serializers.FileField(required=False)
    review_instruction = serializers.CharField()
    template_code = serializers.CharField(required=False, max_length=20)
    project_id = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, max_length=300)
