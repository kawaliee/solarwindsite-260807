from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import WorkspaceViewSet, ProjectViewSet

router = DefaultRouter()
router.register('workspaces', WorkspaceViewSet)
router.register('projects', ProjectViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
