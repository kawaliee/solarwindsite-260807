from django.urls import path

from . import projects, views

urlpatterns = [
    # 검토 프로젝트·배치안 — 찍은 배치를 남겨 두고 다시 불러온다
    path('projects/', projects.project_list, name='windsite-projects'),
    path('projects/<uuid:pk>/', projects.project_detail, name='windsite-project'),
    path('plans/', projects.plan_create, name='windsite-plan-create'),
    path('plans/<uuid:pk>/', projects.plan_detail, name='windsite-plan'),
    # 클릭 좌표 → 필지(PNU) — 태양광 필지 검토의 입구
    path('parcel/', views.parcel_lookup, name='windsite-parcel'),
    # 화면 범위 필지 4등급 채색 — 태양광 발굴 모드의 입구
    path('screen/', views.screen_parcels, name='windsite-screen'),
    path('evaluate-area/', views.evaluate_area, name='windsite-evaluate-area'),
    path('area-report/', views.area_report_download, name='windsite-area-report'),
    path('area-report/cancel/', views.area_report_cancel, name='windsite-area-report-cancel'),
    path('area-report/progress/', views.area_report_progress, name='windsite-area-report-progress'),
    path('compare/', views.compare_sites, name='windsite-compare'),
    path('geocode/', views.geocode_view, name='windsite-geocode'),
    path('permits/', views.permit_roadmap, name='windsite-permits'),
    path('laws/', views.law_list, name='windsite-laws'),
    path('ordinances/', views.ordinance_list, name='windsite-ordinances'),
    path('config/', views.provider_config, name='windsite-config'),
    path('evaluations/', views.evaluation_history, name='windsite-history'),
]
