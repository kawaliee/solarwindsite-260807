from django.urls import path

from . import views

urlpatterns = [
    path('evaluate/', views.evaluate_site, name='windsite-evaluate'),
    path('geocode/', views.geocode_view, name='windsite-geocode'),
    path('permits/', views.permit_roadmap, name='windsite-permits'),
    path('laws/', views.law_list, name='windsite-laws'),
    path('ordinances/', views.ordinance_list, name='windsite-ordinances'),
    path('config/', views.provider_config, name='windsite-config'),
    path('evaluations/', views.evaluation_history, name='windsite-history'),
]
