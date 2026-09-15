"""
재생에너지 입지타당성 검토 시스템 — URL Configuration
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('apps.accounts.urls')),
    path('api/windsite/', include('apps.windsite.urls')),
]
