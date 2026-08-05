from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import FactSheetViewSet, factsheet_schema

router = DefaultRouter()
router.register('factsheets', FactSheetViewSet, basename='factsheet')

urlpatterns = [
    path('factsheets/schema/', factsheet_schema, name='factsheet-schema'),
    path('', include(router.urls)),
]
