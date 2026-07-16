from rest_framework import viewsets
from django_filters.rest_framework import DjangoFilterBackend
from .models import Workspace, Project
from .serializers import WorkspaceSerializer, ProjectSerializer


class WorkspaceViewSet(viewsets.ModelViewSet):
    """워크스페이스 CRUD"""
    queryset = Workspace.objects.all()
    serializer_class = WorkspaceSerializer


class ProjectViewSet(viewsets.ModelViewSet):
    """프로젝트 CRUD — workspace_id 필터 지원"""
    queryset = Project.objects.select_related('workspace').all()
    serializer_class = ProjectSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['workspace', 'is_shared']
