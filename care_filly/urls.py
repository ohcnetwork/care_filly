from django.urls import path

from care_filly.api.viewsets.history import FillyHistoryViewSet
from care_filly.api.viewsets.quota import FillyQuotaViewSet
from care_filly.api.viewsets.scribe import (
    DiscoveryView,
    HealthzView,
    ProcessTemplateView,
    SessionCollectionView,
    SessionDetailView,
    SessionEndView,
    UploadChunkMultipartView,
    UploadChunkView,
)

# Explicit path bindings (no router): the MedScribe Alliance protocol and
# the scribe frontend expect these exact paths, without trailing slashes.
urlpatterns = [
    path("v1/.well-known/medscribealliance", DiscoveryView.as_view()),
    path("v1/sessions", SessionCollectionView.as_view()),
    path("v1/sessions/<str:session_id>", SessionDetailView.as_view()),
    path("v1/sessions/<str:session_id>/end", SessionEndView.as_view()),
    path(
        "v1/sessions/<str:session_id>/process/template/<str:template_id>",
        ProcessTemplateView.as_view(),
    ),
    path("v1/upload/<str:session_id>", UploadChunkMultipartView.as_view()),
    path("v1/upload/<str:session_id>/<str:filename>", UploadChunkView.as_view()),
    # Quota & usage (literal paths before the catch-all detail route)
    path("v1/quota/my", FillyQuotaViewSet.as_view({"get": "my"})),
    path("v1/quota/accept-tnc", FillyQuotaViewSet.as_view({"post": "accept_tnc"})),
    path(
        "v1/preferences/scribe",
        FillyQuotaViewSet.as_view({"get": "preference", "put": "preference"}),
    ),
    path("v1/quota", FillyQuotaViewSet.as_view({"get": "list", "post": "create"})),
    path(
        "v1/quota/<str:external_id>",
        FillyQuotaViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
        ),
    ),
    # Per-user scribe history
    path(
        "v1/history",
        FillyHistoryViewSet.as_view({"get": "list", "delete": "clear"}),
    ),
    path(
        "v1/history/session/<str:session_id>/audio",
        FillyHistoryViewSet.as_view({"post": "upload_audio"}),
    ),
    path(
        "v1/history/<str:external_id>/audio",
        FillyHistoryViewSet.as_view({"get": "audio"}),
    ),
    path(
        "v1/history/<str:external_id>",
        FillyHistoryViewSet.as_view({"delete": "destroy"}),
    ),
    path("healthz", HealthzView.as_view()),
]
