from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ContractTemplateViewSet, ContractDraftViewSet, ContractReviewViewSet

router = DefaultRouter()
router.register('contract-templates', ContractTemplateViewSet)
router.register('contracts/drafts', ContractDraftViewSet)
router.register('contracts/reviews', ContractReviewViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
