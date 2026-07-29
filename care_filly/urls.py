from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from care_filly.api.viewsets import filly, history, quota

urlpatterns = [
    path("v1/sessions", filly.create_session),
    path("v1/sessions/<str:session_id>", filly.session_detail),
    path("v1/sessions/<str:session_id>/chunks", filly.upload_chunk),
    path("v1/sessions/<str:session_id>/end", filly.end_session),
    path(
        "v1/sessions/<str:session_id>/process/template/<str:template_id>",
        filly.process_template,
    ),
    # Quota & usage (literal paths before the catch-all detail route)
    path("v1/quota/my", quota.my_quota),
    path("v1/quota/accept-tnc", quota.accept_tnc),
    path("v1/preferences/filly", quota.filly_preference),
    path("v1/quota", quota.quota_collection),
    path("v1/quota/<str:external_id>", quota.quota_detail),
    # Per-user filly history
    path("v1/history", history.history_collection),
    path(
        "v1/history/session/<str:session_id>/audio",
        history.upload_history_audio,
    ),
    path("v1/history/<str:external_id>/audio", history.history_audio),
    path("v1/history/<str:external_id>", history.history_detail),
    path("healthz", filly.healthz),
]

# Every care_filly endpoint authenticates via the CARE JWT (Authorization
# header), not session cookies, so Django's cookie-based CSRF protection does
# not apply and would otherwise reject the stateless POST/PUT/PATCH/DELETE
# requests with a 403 before the view runs. Exempt all routes in one place
# rather than decorating each view individually.
for _pattern in urlpatterns:
    _pattern.callback = csrf_exempt(_pattern.callback)
