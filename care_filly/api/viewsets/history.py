"""User-scoped filly history built on ``FillySession`` rows.

A finished session *is* a history entry (statuses in ``HISTORY_STATUSES``).
Every endpoint operates strictly on ``request.user``'s own sessions —
enforced through the ``FillyAccess`` authorization handler — so no user
can list, read or delete another user's history.

- GET    v1/history                              -> {count, results}
- DELETE v1/history                              -> clear the user's history
- DELETE v1/history/<external_id>                -> delete one entry
- GET    v1/history/<external_id>/audio          -> redirect to a signed URL
- POST   v1/history/session/<session_id>/audio   -> attach the recording
"""

import logging

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.utils import timezone

from care.security.authorization import AuthorizationController

from care_filly.api.common import authenticate, error, parse_uuid
from care_filly.models import HISTORY_STATUSES, FillySession

logger = logging.getLogger("care_filly")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB


def _require_user(request: HttpRequest):
    err, user = authenticate(request)
    return err, user


def _history_qs(user):
    base = FillySession.objects.filter(status__in=HISTORY_STATUSES)
    return AuthorizationController.call("get_filly_history", user, base)


def _structured_data(session: FillySession) -> dict | None:
    for result in (session.template_results or {}).values():
        if isinstance(result, dict) and result.get("status") == "success":
            return result.get("data")
    return None


def _history_dict(session: FillySession) -> dict:
    return {
        "id": str(session.external_id),
        "session_id": str(session.external_id),
        "facility_external_id": str(session.facility.external_id),
        "started_at": session.started_at.isoformat()
        if session.started_at
        else session.created_date.isoformat(),
        "duration_seconds": session.duration_seconds,
        "status": "completed" if session.status == "completed" else "failed",
        "transcript": session.transcript,
        "structured_data": _structured_data(session),
        "template_results": session.template_results or {},
        "error": (session.processing_errors or [{}])[0].get("message")
        if session.processing_errors
        else None,
        "has_audio": session.has_audio(),
        "audio_mime_type": session.audio_mime_type,
    }


def history_collection(request: HttpRequest) -> JsonResponse:
    if request.method not in ("GET", "DELETE"):
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    if request.method == "DELETE":
        deleted = _history_qs(user).update(deleted=True, modified_date=timezone.now())
        return JsonResponse({"deleted": deleted})

    qs = _history_qs(user).select_related("facility").order_by("-started_at")
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


def _get_entry(user, external_id: str) -> FillySession | None:
    if parse_uuid(external_id) is None:
        return None
    return _history_qs(user).filter(external_id=external_id).first()


def _entry_not_found() -> JsonResponse:
    return error("not_found", "History entry not found", 404)


def history_detail(request: HttpRequest, external_id: str) -> JsonResponse:
    if request.method != "DELETE":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err
    entry = _get_entry(user, external_id)
    if entry is None:
        return _entry_not_found()
    entry.delete()  # soft delete — row and audio are kept for audit
    return JsonResponse({"deleted": 1})


def history_audio(request: HttpRequest, external_id: str) -> HttpResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err
    entry = _get_entry(user, external_id)
    if entry is None or not entry.has_audio():
        return _entry_not_found()
    return HttpResponseRedirect(entry.read_audio_url())


def upload_history_audio(request: HttpRequest, session_id: str) -> JsonResponse:
    """Attach the client's locally captured recording to its session row."""
    if request.method != "POST":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    file = request.FILES.get("file")
    if file is None:
        return error("missing_file", "No audio file", 400)
    if file.size > MAX_AUDIO_BYTES:
        return error("too_large", "Audio file too large", 413)

    entry = _get_entry(user, session_id) or (
        _history_qs(user).filter(external_id=session_id).first()
        if parse_uuid(session_id)
        else None
    )
    if entry is None:
        return _entry_not_found()

    mime = file.content_type or "audio/webm"
    entry.save_audio(file.read(), mime)  # uploads to object storage
    update_fields = ["internal_name", "audio_mime_type", "meta", "modified_date"]

    try:
        duration = int(float(request.POST.get("duration", "")))
        if duration > 0:
            entry.duration_seconds = duration
            update_fields.append("duration_seconds")
    except ValueError:
        pass

    entry.save(update_fields=update_fields)
    return JsonResponse({"id": str(entry.external_id), "has_audio": True})
