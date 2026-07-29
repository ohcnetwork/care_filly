"""User-scoped scribe history: recording + endpoints.

History rows are written server-side when a session finalizes, so a
user's past sessions follow them across browsers and devices. Every
endpoint operates strictly on ``request.user``'s own rows — no user can
list or delete another user's history.

- GET    v1/history                          -> {count, results} (newest first)
- DELETE v1/history                          -> clear the user's entire history
- DELETE v1/history/<external_id>            -> delete one entry
- GET    v1/history/<external_id>/audio      -> redirect to a signed recording URL
- POST   v1/history/session/<session_id>/audio -> attach the client's recording
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from care_filly.models import FillyHistory
from care_filly.quota import parse_facility_id

logger = logging.getLogger("care_filly")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB


def record_history(
    session: dict,
    status: str,
    transcript: str,
    structured_data: Optional[dict],
    error: Optional[str] = None,
) -> None:
    """Persist a FillyHistory row for a finalized session.

    Best-effort: history must never break finalize. Skipped in
    standalone/static-token mode where there is no CARE user to own it.
    """
    user_id = session.get("user_id")
    if user_id is None:
        return

    started_at = timezone.now()
    try:
        started_at = datetime.fromisoformat(session["created_at"])
    except (KeyError, ValueError, TypeError):
        pass

    try:
        FillyHistory.objects.create(
            user_id=user_id,
            facility_external_id=parse_facility_id(session.get("facility_id")),
            session_id=session["session_id"],
            started_at=started_at,
            duration_seconds=20 * len(session.get("chunk_indexes") or []),
            status="completed" if status == "completed" else "failed",
            transcript=transcript or None,
            structured_data=structured_data or None,
            error=error,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "failed to record history for session %s", session["session_id"]
        )


def _require_user(request: HttpRequest):
    from care_filly.api.viewsets.quota import _require_user as require_user

    return require_user(request)


def _history_dict(entry: FillyHistory) -> dict:
    return {
        "id": str(entry.external_id),
        "session_id": entry.session_id,
        "facility_external_id": str(entry.facility_external_id)
        if entry.facility_external_id
        else None,
        "started_at": entry.started_at.isoformat(),
        "duration_seconds": entry.duration_seconds,
        "status": entry.status,
        "transcript": entry.transcript,
        "structured_data": entry.structured_data,
        "error": entry.error,
        "has_audio": entry.has_audio(),
        "audio_mime_type": entry.audio_mime_type,
    }


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def history_collection(request: HttpRequest) -> JsonResponse:
    err, user = _require_user(request)
    if err:
        return err

    if request.method == "DELETE":
        # Soft delete — rows and audio are kept for audit, just hidden.
        # `objects` already excludes deleted rows, so this only ever
        # touches the user's still-visible entries.
        deleted = FillyHistory.objects.filter(user=user).update(
            deleted=True, modified_date=timezone.now()
        )
        return JsonResponse({"deleted": deleted})

    qs = FillyHistory.objects.filter(user=user).order_by("-started_at")
    try:
        limit = min(int(request.GET.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        limit, offset = DEFAULT_PAGE_SIZE, 0
    return JsonResponse(
        {
            "count": qs.count(),
            "results": [_history_dict(e) for e in qs[offset : offset + limit]],
        }
    )


@csrf_exempt
@require_http_methods(["DELETE"])
def history_detail(request: HttpRequest, external_id: str) -> JsonResponse:
    err, user = _require_user(request)
    if err:
        return err
    entry = _get_entry(user, external_id)
    if entry is None:
        return _entry_not_found()
    entry.delete()  # soft delete — row and audio are kept for audit
    return JsonResponse({"deleted": 1})


def _get_entry(user, external_id: str) -> Optional[FillyHistory]:
    entry_uuid = parse_facility_id(external_id)  # generic "parse UUID or None"
    if entry_uuid is None:
        return None
    return FillyHistory.objects.filter(user=user, external_id=entry_uuid).first()


def _entry_not_found() -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "not_found", "message": "History entry not found"}},
        status=404,
    )


@csrf_exempt
@require_http_methods(["GET"])
def history_audio(request: HttpRequest, external_id: str) -> HttpResponse:
    """Redirect to a short-lived signed URL for the user's own recording.

    The audio itself lives in object storage (Minio/S3) — the app server
    never proxies the bytes, same as CARE's own file uploads.
    """
    err, user = _require_user(request)
    if err:
        return err
    entry = _get_entry(user, external_id)
    if entry is None or not entry.has_audio():
        return _entry_not_found()
    return HttpResponseRedirect(entry.read_audio_url())


@csrf_exempt
@require_http_methods(["POST"])
def upload_history_audio(request: HttpRequest, session_id: str) -> JsonResponse:
    """Attach the client's locally captured recording to its history row.

    Keyed by session_id (the client never sees the row's external_id
    before listing). Only the row's owner can attach audio.
    """
    err, user = _require_user(request)
    if err:
        return err

    file = request.FILES.get("file")
    if file is None:
        return JsonResponse(
            {"error": {"code": "missing_file", "message": "No audio file"}},
            status=400,
        )
    if file.size > MAX_AUDIO_BYTES:
        return JsonResponse(
            {"error": {"code": "too_large", "message": "Audio file too large"}},
            status=413,
        )

    entry = (
        FillyHistory.objects.filter(user=user, session_id=session_id)
        .order_by("-created_date")
        .first()
    )
    if entry is None:
        return _entry_not_found()

    mime = file.content_type or "audio/webm"
    entry.save_audio(file.read(), mime)  # uploads to object storage
    update_fields = ["internal_name", "audio_mime_type", "meta"]

    try:
        duration = int(float(request.POST.get("duration", "")))
        if duration > 0:
            entry.duration_seconds = duration
            update_fields.append("duration_seconds")
    except ValueError:
        pass

    entry.save(update_fields=update_fields)
    return JsonResponse({"id": str(entry.external_id), "has_audio": True})
