"""Medispeak session/token seam.

Two endpoints only: create a Medispeak session (server-side, using the
account secret) and mint a scoped session token for the browser SDK. All
actual recording, chunk upload, commit and result polling happens directly
between the browser and Medispeak — this backend never sees the audio.
"""

import logging

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
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
from care_filly.models import FillyUsage, MedispeakSession
from care_filly.providers.base import ProviderError
from care_filly.quota import check_can_filly

logger = logging.getLogger("care_filly")


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
    if quota_error := check_can_filly(user, facility):
        return JsonResponse({"error": quota_error}, status=403)

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
    except ProviderError as exc:
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
    except ProviderError as exc:
        logger.exception("medispeak token mint failed")
        return error("medispeak_error", str(exc), 502)

    return JsonResponse(
        {"token": token.get("token"), "expires_at": token.get("expires_at")}
    )


@require_http_methods(["POST"])
def finalize_medispeak_session(request: HttpRequest, session_id: str) -> JsonResponse:
    """Record quota usage for a finished Medispeak session (idempotent).

    Usage is pulled from Medispeak itself (the authoritative source), not
    trusted from the browser, and only ever recorded once per session.
    """
    err, user = authenticate(request)
    if err:
        return err

    if parse_uuid(session_id) is None:
        return error("session_not_found", "Unknown session", 404)

    with transaction.atomic():
        row = (
            MedispeakSession.objects.select_for_update()
            .filter(external_id=session_id, user=user, deleted=False)
            .first()
        )
        if row is None:
            return error("session_not_found", "Unknown session", 404)
        if row.usage_recorded_at is not None:
            return JsonResponse({"status": "already_recorded"})

        try:
            session = medispeak_client.get_session(row.medispeak_session_id)
        except ProviderError as exc:
            logger.exception("medispeak usage fetch failed")
            return error("medispeak_error", str(exc), 502)

        usage = session.get("usage") or {}
        # Medispeak reports one combined token count, not input/output split.
        usage_row = FillyUsage.objects.create(
            user=row.user,
            facility=row.facility,
            input_tokens=int(usage.get("total_tokens") or 0),
            audio_seconds=int(usage.get("audio_seconds") or 0),
        )
        row.usage_recorded_at = timezone.now()
        row.save(update_fields=["usage_recorded_at", "modified_date"])

    return JsonResponse(
        {
            "status": "recorded",
            "input_tokens": usage_row.input_tokens,
            "audio_seconds": usage_row.audio_seconds,
        }
    )
