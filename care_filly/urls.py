from django.urls import path

from care_filly.api.viewsets import history, quota, scribe

urlpatterns = [
    path("v1/.well-known/medscribealliance", scribe.discovery),
    path("v1/sessions", scribe.create_session),
    path("v1/sessions/<str:session_id>", scribe.session_detail),
    path("v1/sessions/<str:session_id>/end", scribe.end_session),
    path(
        "v1/sessions/<str:session_id>/process/template/<str:template_id>",
        scribe.process_template,
    ),
    path("v1/upload/<str:session_id>", scribe.upload_chunk_multipart),
    path("v1/upload/<str:session_id>/<str:filename>", scribe.upload_chunk),
    # Quota & usage (literal paths before the catch-all detail route)
    path("v1/quota/my", quota.my_quota),
    path("v1/quota/accept-tnc", quota.accept_tnc),
    path("v1/preferences/scribe", quota.scribe_preference),
    path("v1/quota", quota.quota_collection),
    path("v1/quota/<str:external_id>", quota.quota_detail),
    # Per-user scribe history
    path("v1/history", history.history_collection),
    path(
        "v1/history/session/<str:session_id>/audio",
        history.upload_history_audio,
    ),
    path("v1/history/<str:external_id>/audio", history.history_audio),
    path("v1/history/<str:external_id>", history.history_detail),
    path("healthz", scribe.healthz),
]
