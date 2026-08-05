from rest_framework import serializers

from .models import ProjectFactSheet


class FactSheetListSerializer(serializers.ModelSerializer):
    """목록용 — data 본체는 제외해 응답을 가볍게 유지한다."""
    project_name = serializers.CharField(source='project.name', read_only=True, default='')
    completeness = serializers.SerializerMethodField()
    capacity_ac = serializers.SerializerMethodField()

    class Meta:
        model = ProjectFactSheet
        fields = [
            'id', 'project', 'project_name', 'name', 'aliases', 'spc_name',
            'stage', 'as_of_date', 'author', 'pjt_folder',
            'published_at', 'published_path', 'completeness', 'capacity_ac',
            'created_at', 'updated_at',
        ]

    def get_completeness(self, obj):
        return obj.completeness()

    def get_capacity_ac(self, obj):
        return obj.value('basic', 'capacity_ac')


class FactSheetSerializer(FactSheetListSerializer):
    """상세/저장용 — 입력값 본체 포함"""

    class Meta(FactSheetListSerializer.Meta):
        fields = FactSheetListSerializer.Meta.fields + ['data', 'schema_version', 'markdown']
        read_only_fields = ['markdown', 'published_at', 'published_path']

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('대표 사업명은 필수입니다.')
        return value

    def validate_aliases(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('별칭은 문자열 배열이어야 합니다.')
        return [str(v).strip() for v in value if str(v).strip()]

    def validate_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('data 는 객체여야 합니다.')
        return value
