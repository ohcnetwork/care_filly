"""Static-token authentication for standalone / service access.

CARE's own JWT auth (``config.authentication.CustomJWTAuthentication``)
comes for free via the DRF default authentication classes — this module
only adds the optional ``FILLY_AUTH_TOKEN`` static token used for
standalone testing and service-to-service access.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication

from care_filly.settings import plugin_settings


class FillyServiceUser(AnonymousUser):
    """Authenticated-but-userless principal for static-token access.

    ``pk`` stays ``None`` so quota enforcement and history recording —
    which only apply to real CARE users — are skipped naturally.
    """

    username = "filly-service"

    @property
    def is_authenticated(self) -> bool:
        return True


def care_user(request):
    """The real CARE user behind a request, or None for service access."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "pk", None):
        return user
    return None


class FillyStaticTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <FILLY_AUTH_TOKEN>` — checked before JWT.

    Returns None (falls through to the JWT/session authenticators) when
    no static token is configured or the header doesn't match it.
    """

    def authenticate(self, request):
        static_token = plugin_settings.FILLY_AUTH_TOKEN
        if not static_token:
            return None
        auth_header = request.headers.get("Authorization", "")
        if auth_header == f"Bearer {static_token}":
            return (FillyServiceUser(), static_token)
        return None

    def authenticate_header(self, request):
        return "Bearer"
