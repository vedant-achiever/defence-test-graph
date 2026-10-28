from django.urls import path
from . import views

urlpatterns = [
    path('', views.index_view, name='index'),
    path('health', views.health_check, name='health_check'),
    path('health/', views.health_check, name='health_check_slash'),
    
    # New endpoints for large file handling
    path('upload', views.upload_telemetry, name='upload_telemetry'),
    path('upload/', views.upload_telemetry, name='upload_telemetry_slash'),
    path('combine', views.combine_telemetry, name='combine_telemetry'),
    path('combine/', views.combine_telemetry, name='combine_telemetry_slash'),
    path('export', views.export_telemetry, name='export_telemetry'),
    path('export/', views.export_telemetry, name='export_telemetry_slash'),
    path('averaged-data', views.averaged_data, name='averaged_data'),
    path('averaged-data/', views.averaged_data, name='averaged_data_slash'),
    path('actual-data', views.actual_data, name='actual_data'),
    path('actual-data/', views.actual_data, name='actual_data_slash'),
    
    # Existing AI endpoints
    path('gemini/analyze', views.analyze_telemetry, name='analyze_telemetry'),
    path('gemini/analyze/', views.analyze_telemetry, name='analyze_telemetry_slash'),
    path('gemini/chat', views.chat_telemetry, name='chat_telemetry'),
    path('gemini/chat/', views.chat_telemetry, name='chat_telemetry_slash'),
    path('gemini/vision', views.vision_telemetry, name='vision_telemetry'),
    path('gemini/vision/', views.vision_telemetry, name='vision_telemetry_slash'),
    path('ai/semantic-search', views.semantic_search, name='semantic_search'),
    path('ai/semantic-search/', views.semantic_search, name='semantic_search_slash'),
]
