"""In-house filly session endpoints.

Mounted by CARE at ``/api/care_filly/`` — the in-house frontend recorder
talks to these routes directly (no third-party SDK, no fetch shim):

* auth is CARE-JWT only (``care_filly.api.common.authenticate``) on every
  request — including chunk uploads, which carry the Bearer token in the
  ``Authorization`` header (no capability token / presigned URL),
* the ``FillySession`` DB row is the source of truth,
* chunk audio + transcripts live in the cache hot path,
* transcription/finalization run on Celery (not daemon threads),
* per-session access is owner-only (``FillyAccess`` authorization).
"""

import logging
from datetime import timedelta

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from care.security.authorization import AuthorizationController
from care_filly import chunk_store
from care_filly.api.common import authenticate, body, error, resolve_facility
from care_filly.models import UPLOADABLE_STATUSES, FillySession, SessionStatus
from care_filly.providers import resolve_language
from care_filly.quota import check_can_filly
from care_filly.settings import mock_mode, plugin_settings
from care_filly.tasks import finalize_session, transcribe_chunk

logger = logging.getLogger("care_filly")


# ---------------------------------------------------------------------------
# helpers


def _session_or_none(session_id: str) -> FillySession | None:
    from care_filly.api.common import parse_uuid

    if parse_uuid(session_id) is None:
        return None
    return FillySession.objects.filter(external_id=session_id).first()


def _not_found() -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "session_not_found", "message": "Unknown session"}},
        status=404,
    )


def _authorize_owner(user, session: FillySession) -> bool:
    return AuthorizationController.call("can_view_filly_session", user, session)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# health


@require_http_methods(["GET"])
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "mock": mock_mode()})


# ---------------------------------------------------------------------------
# session lifecycle


@require_http_methods(["POST"])
def create_session(request: HttpRequest) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    b = body(request)

    additional_data = b.get("additional_data") or {}
    facility_ext = b.get("facility_id") or additional_data.get("facility_id")
    facility = resolve_facility(facility_ext)
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

    now = timezone.now()
    session = FillySession.objects.create(
        user=user,
        facility=facility,
        status=SessionStatus.CREATED,
        templates=b.get("templates") or [],
        language_hint=b.get("language_hint") or [],
        additional_data=additional_data,
        patient_details=b.get("patient_details"),
        started_at=now,
        expires_at=now + timedelta(seconds=plugin_settings.SESSION_TTL_SECONDS),
        created_by=user,
        updated_by=user,
    )
    sid = str(session.external_id)
    logger.info("session %s created (templates=%s)", sid, session.templates)
    return JsonResponse(
        {
            "session_id": sid,
            "status": session.status,
            "created_at": _iso(session.created_date),
            "expires_at": _iso(session.expires_at),
            "patient_details": session.patient_details,
        }
    )


def _reject_upload(user, session: FillySession) -> JsonResponse | None:
    """Guard clauses for chunk uploads — returns an error response or None."""
    if not _authorize_owner(user, session):
        return _not_found()
    if session.expires_at < timezone.now():
        return JsonResponse(
            {"error": {"code": "session_expired", "message": "Session expired"}},
            status=410,
        )
    if session.status not in UPLOADABLE_STATUSES:
        return JsonResponse(
            {"error": {"code": "not_recording", "message": "Session not recording"}},
            status=409,
        )
    return None


@require_http_methods(["POST"])
def upload_chunk(request: HttpRequest, session_id: str) -> JsonResponse:
    """In-house chunk upload: JWT-authenticated multipart POST.

    The in-house recorder uploads each VAD-detected speech segment as a
    standard multipart form (``file`` + ``index``) with the CARE JWT in the
    ``Authorization`` header — no capability token, no presigned URL.
    """
    err, user = authenticate(request)
    if err:
        return err
    session = _session_or_none(session_id)
    if session is None:
        return _not_found()
    if reject := _reject_upload(user, session):
        return reject

    file = request.FILES.get("file")
    audio = file.read() if file else b""
    if not audio:
        return error("empty_audio", "Empty audio body", 400)

    raw_index = request.POST.get("index")
    try:
        idx = (
            int(raw_index)
            if raw_index is not None
            else len(session.chunk_indexes or [])
        )
    except (TypeError, ValueError):
        idx = len(session.chunk_indexes or [])

    ext = file.name.rsplit(".", 1)[-1] if file and "." in file.name else "wav"
    filename = f"{idx}.{ext}"

    chunk_store.store_chunk_audio(session_id, idx, audio)
    chunk_store.register_chunk(session_id, idx, filename)

    with chunk_store.session_lock(session_id):
        row = FillySession.objects.filter(external_id=session_id).first()
        if row is None:
            return _not_found()
        indexes = list(row.chunk_indexes or [])
        files = list(row.audio_files or [])
        if idx not in indexes:
            indexes.append(idx)
            files.append(filename)
        row.chunk_indexes = indexes
        row.audio_files = files
        row.status = SessionStatus.RECORDING
        row.save(
            update_fields=["chunk_indexes", "audio_files", "status", "modified_date"]
        )

    language = resolve_language(session.language_hint or [])
    # Transcription starts NOW, while the doctor is still talking.
    transcribe_chunk.delay(session_id, idx, filename, language)

    return JsonResponse({"success": True, "index": idx, "file": filename})


@require_http_methods(["POST"])
def end_session(request: HttpRequest, session_id: str) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    session = _session_or_none(session_id)
    if session is None:
        return _not_found()
    if not _authorize_owner(user, session):
        return _not_found()

    FillySession.objects.filter(pk=session.pk).update(
        status=SessionStatus.PROCESSING, modified_date=timezone.now()
    )
    finalize_session.delay(session_id)

    return JsonResponse(
        {
            "session_id": session_id,
            "status": SessionStatus.PROCESSING,
            "message": "Processing started",
            "audio_files_received": len(session.audio_files or []),
            "audio_files": session.audio_files or [],
        }
    )


def session_detail(request: HttpRequest, session_id: str) -> HttpResponse:
    if request.method == "PATCH":
        return _patch_session(request, session_id)
    if request.method == "GET":
        return _get_session_status(request, session_id)
    return HttpResponse(status=405)


def _get_session_status(request: HttpRequest, session_id: str) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    session = _session_or_none(session_id)
    if session is None:
        return _not_found()
    if not _authorize_owner(user, session):
        return _not_found()

    template_results = session.template_results or {}
    if template_results:
        templates = [{tid: result} for tid, result in template_results.items()]
    elif session.status == SessionStatus.PROCESSING:
        templates = [
            {tid: {"status": "in-progress"}} for tid in (session.templates or [])
        ]
    else:
        templates = []

    # Expose the transcript as soon as all chunks are transcribed — the FE
    # shows it immediately while extraction is still running.
    transcript = session.transcript
    indexes = session.chunk_indexes or []
    chunks = chunk_store.get_chunks(session_id, indexes)
    if transcript is None and chunks and chunk_store.all_chunks_done(chunks, indexes):
        transcript = chunk_store.assemble_transcript(chunks)

    return JsonResponse(
        {
            "session_id": session_id,
            "status": session.status,
            "created_at": _iso(session.created_date),
            "expires_at": _iso(session.expires_at),
            "completed_at": _iso(session.completed_at),
            "model_used": "pro",
            "language_detected": None,
            "audio_files_received": len(session.audio_files or []),
            "audio_files": session.audio_files or [],
            "audio_files_processed": sum(
                1 for c in chunks.values() if c.get("text") is not None
            ),
            "additional_data": session.additional_data,
            "templates": templates,
            "transcript": transcript,
            "processing_errors": session.processing_errors or [],
            "patient_details": session.patient_details,
            "usage": session.usage,
        }
    )


def _patch_session(request: HttpRequest, session_id: str) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    session = _session_or_none(session_id)
    if session is None:
        return _not_found()
    if not _authorize_owner(user, session):
        return _not_found()

    b = body(request)
    update_fields = ["modified_date"]
    for key in ("templates", "language_hint", "patient_details"):
        if b.get(key):
            setattr(session, key, b[key])
            update_fields.append(key)
    if b.get("additional_data"):
        session.additional_data = {
            **(session.additional_data or {}),
            **b["additional_data"],
        }
        update_fields.append("additional_data")
    if len(update_fields) > 1:
        session.save(update_fields=update_fields)

    return JsonResponse(
        {
            "session_id": session_id,
            "status": session.status,
            "message": "Session updated",
        }
    )


@require_http_methods(["POST"])
def process_template(
    request: HttpRequest, session_id: str, template_id: str
) -> JsonResponse:
    err, user = authenticate(request)
    if err:
        return err
    session = _session_or_none(session_id)
    if session is None:
        return _not_found()
    if not _authorize_owner(user, session):
        return _not_found()

    with chunk_store.session_lock(session_id):
        row = FillySession.objects.filter(external_id=session_id).first()
        if row is None:
            return _not_found()
        templates = list(row.templates or [])
        if template_id not in templates:
            templates.append(template_id)
        results = dict(row.template_results or {})
        results.pop(template_id, None)
        row.templates = templates
        row.template_results = results
        row.status = SessionStatus.PROCESSING
        row.finalize_attempts = 0
        row.save(
            update_fields=[
                "templates",
                "template_results",
                "status",
                "finalize_attempts",
                "modified_date",
            ]
        )

    finalize_session.delay(session_id)
    return JsonResponse(
        {
            "session_id": session_id,
            "template_id": template_id,
            "status": SessionStatus.PROCESSING,
            "message": "Template processing triggered",
        }
    )
