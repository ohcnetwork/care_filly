from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from care_filly.api.viewsets import medispeak

urlpatterns = [
    # Medispeak session/token seam — the browser talks to Medispeak directly
    # after this; these are the only routes that hold the account secret.
    path("v1/medispeak/sessions", medispeak.create_medispeak_session),
    path(
        "v1/medispeak/sessions/<str:session_id>/token",
        medispeak.mint_medispeak_token,
    ),
    path("healthz", medispeak.healthz),
]

# Every care_filly endpoint authenticates via the CARE JWT (Authorization
# header), not session cookies, so Django's cookie-based CSRF protection does
# not apply and would otherwise reject the stateless POST requests with a 403
# before the view runs. Exempt all routes in one place rather than decorating
# each view individually.
for _pattern in urlpatterns:
    _pattern.callback = csrf_exempt(_pattern.callback)
