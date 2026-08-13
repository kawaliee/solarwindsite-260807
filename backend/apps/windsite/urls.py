from django.urls import path

from . import views

urlpatterns = [
    path('evaluate/', views.evaluate_site, name='windsite-evaluate'),
    path('evaluate-area/', views.evaluate_area, name='windsite-evaluate-area'),
    path('area-report/', views.area_report_download, name='windsite-area-report'),
    path('area-report/cancel/', views.area_report_cancel, name='windsite-area-report-cancel'),
    path('area-report/progress/', views.area_report_progress, name='windsite-area-report-progress'),
    path('compare/', views.compare_sites, name='windsite-compare'),
    path('report/', views.evaluation_report, name='windsite-report'),
    path('geocode/', views.geocode_view, name='windsite-geocode'),
    path('permits/', views.permit_roadmap, name='windsite-permits'),
    path('laws/', views.law_list, name='windsite-laws'),
    path('ordinances/', views.ordinance_list, name='windsite-ordinances'),
    path('config/', views.provider_config, name='windsite-config'),
    path('evaluations/', views.evaluation_history, name='windsite-history'),
]
