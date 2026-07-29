"""Shared helpers for the care_filly HTTP layer.

Authentication is JWT-only: every request must carry the logged-in CARE
user's access token (the frontend sends it as ``Authorization: Bearer
<token>``). The old static ``FILLY_AUTH_TOKEN`` / standalone fallback has
been removed — this plugin only ever runs inside CARE.
"""

import json
import uuid

from django.http import HttpRequest, JsonResponse


def authenticate(request: HttpRequest) -> tuple[JsonResponse | None, object | None]:
    """Return ``(error_response, user)`` — validates the CARE JWT."""
    from config.authentication import CustomJWTAuthentication

    try:
        result = CustomJWTAuthentication().authenticate(request)
    except Exception:  # noqa: BLE001 — invalid/expired token
        result = None

    if result:
        return None, result[0]

    return (
        JsonResponse(
            {"error": {"code": "unauthorized", "message": "Invalid or missing token"}},
            status=401,
        ),
        None,
    )


def body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def parse_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def resolve_facility(external_id: object):
    """Resolve a CARE ``Facility`` by external id (or ``None``)."""
    facility_uuid = parse_uuid(external_id)
    if facility_uuid is None:
        return None
    from care.facility.models.facility import Facility

    return Facility.objects.filter(external_id=facility_uuid).first()
