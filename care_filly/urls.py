from django.urls import path

from . import history_views, quota_views, views

urlpatterns = [
    path("v1/.well-known/medscribealliance", views.discovery),
    path("v1/sessions", views.create_session),
    path("v1/sessions/<str:session_id>", views.session_detail),
    path("v1/sessions/<str:session_id>/end", views.end_session),
    path(
        "v1/sessions/<str:session_id>/process/template/<str:template_id>",
        views.process_template,
    ),
    path("v1/upload/<str:session_id>", views.upload_chunk_multipart),
    path("v1/upload/<str:session_id>/<str:filename>", views.upload_chunk),
    # Quota & usage (literal paths before the catch-all detail route)
    path("v1/quota/my", quota_views.my_quota),
    path("v1/quota/accept-tnc", quota_views.accept_tnc),
    path("v1/preferences/scribe", quota_views.scribe_preference),
    path("v1/quota", quota_views.quota_collection),
    path("v1/quota/<str:external_id>", quota_views.quota_detail),
    # Per-user scribe history
    path("v1/history", history_views.history_collection),
    path(
        "v1/history/session/<str:session_id>/audio",
        history_views.upload_history_audio,
    ),
    path("v1/history/<str:external_id>/audio", history_views.history_audio),
    path("v1/history/<str:external_id>", history_views.history_detail),
    path("healthz", views.healthz),
]
