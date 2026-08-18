"""Medispeak session/token seam.

Two endpoints only: create a Medispeak session (server-side, using the
account secret) and mint a scoped session token for the browser SDK. All
actual recording, chunk upload, commit and result polling happens directly
between the browser and Medispeak — this backend never sees the audio.
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from care.security.authorization import AuthorizationController
from care_filly import medispeak_client
from care_filly.api.common import (
    authenticate,
    body,
    error,
    parse_uuid,
    resolve_facility,
)
from care_filly.http import MedispeakError
from care_filly.models import MedispeakSession

logger = logging.getLogger("care_filly")


@require_http_methods(["GET"])
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def create_medispeak_session(request: HttpRequest) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    b = body(request)

    facility = resolve_facility(b.get("facility_id"))
    if facility is None:
        return error(
            "facility_required",
            "A valid facility_id is required to start a filly session.",
            400,
        )
    if not AuthorizationController.call("can_use_filly", user, facility):
        return error("forbidden", "You do not have permission to use filly.", 403)

    outputs: list[dict] = [{"type": "transcript"}]
    fields = b.get("fields")
    if fields:
        outputs.append({"type": "form", "fields": fields})

    try:
        session = medispeak_client.create_session(
            outputs=outputs,
            language=b.get("language"),
            mode=b.get("mode", "consultation"),
        )
        token = medispeak_client.mint_session_token(str(session["id"]))
    except MedispeakError as exc:
        logger.exception("medispeak session creation failed")
        return error("medispeak_error", str(exc), 502)

    row = MedispeakSession.objects.create(
        user=user,
        facility=facility,
        medispeak_session_id=str(session["id"]),
        created_by=user,
        updated_by=user,
    )
    return JsonResponse(
        {
            "session_id": str(row.external_id),
            "medispeak_session_id": str(session["id"]),
            "expires_at": session.get("expires_at"),
            "token": token.get("token"),
            "token_expires_at": token.get("expires_at"),
        },
        status=201,
    )


@require_http_methods(["POST"])
def mint_medispeak_token(request: HttpRequest, session_id: str) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err

    if parse_uuid(session_id) is None:
        return error("session_not_found", "Unknown session", 404)
    row = MedispeakSession.objects.filter(
        external_id=session_id, user=user, deleted=False
    ).first()
    if row is None:
        return error("session_not_found", "Unknown session", 404)

    try:
        token = medispeak_client.mint_session_token(row.medispeak_session_id)
    except MedispeakError as exc:
        logger.exception("medispeak token mint failed")
        return error("medispeak_error", str(exc), 502)

    return JsonResponse(
        {"token": token.get("token"), "expires_at": token.get("expires_at")}
    )
