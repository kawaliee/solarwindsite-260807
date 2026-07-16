from rest_framework import serializers
from .models import Workspace, Project


class WorkspaceSerializer(serializers.ModelSerializer):
    project_count = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = ['id', 'name', 'slug', 'description', 'settings',
                  'created_by', 'created_at', 'updated_at', 'project_count']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_project_count(self, obj):
        return obj.projects.count()


class ProjectSerializer(serializers.ModelSerializer):
    workspace_name = serializers.CharField(source='workspace.name', read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'workspace', 'workspace_name', 'name', 'description',
                  'is_shared', 'icon', 'color', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
