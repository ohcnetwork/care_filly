"""MedScribe Alliance Protocol (v0.1) endpoints as Django views.

Mounted by CARE at /api/care_filly/ — the frontend sets
REACT_SCRIBE_BE_URL=<care-api-origin>/api/care_filly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from typing import Optional

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import engine, quota, store
from .plugin_settings import get_setting, mock_mode

logger = logging.getLogger("care_filly")

SUPPORTED_LANGUAGES = [
    "auto_detect",
    "en",
    "en-IN",
    "en-US",
    "hi",
    "gu",
    "kn",
    "ml",
    "ta",
    "te",
    "bn",
    "mr",
    "pa",
    "ur",
    "es",
    "fr",
    "de",
    "pt",
    "ar",
]

FINALIZE_TIMEOUT_SECONDS = 90


def _authenticate(
    request: HttpRequest,
) -> tuple[Optional[JsonResponse], Optional[object]]:
    """Accept either the CARE user's JWT (default) or a static token.

    The frontend sends the logged-in CARE user's access token (same
    convention as care_filly_fe), validated with CARE's own JWT auth.
    A static FILLY_AUTH_TOKEN config is supported as an alternative
    (e.g. standalone/testing).

    Returns (error_response, user). `user` is the authenticated CARE user
    or None when authenticated via static token / standalone mode.
    """
    static_token = get_setting("FILLY_AUTH_TOKEN")
    auth_header = request.headers.get("Authorization", "")
    if static_token and auth_header == f"Bearer {static_token}":
        return None, None

    try:
        from config.authentication import CustomJWTAuthentication

        result = CustomJWTAuthentication().authenticate(request)
        if result:
            return None, result[0]
    except ImportError:
        # Not running inside CARE (standalone tests) — fall through.
        if not static_token:
            return None, None
    except Exception:  # noqa: BLE001 — invalid/expired token
        pass

    return (
        JsonResponse(
            {"error": {"code": "unauthorized", "message": "Invalid or missing token"}},
            status=401,
        ),
        None,
    )


def _auth_error(request: HttpRequest) -> Optional[JsonResponse]:
    error, _user = _authenticate(request)
    return error


def _not_found() -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "session_not_found", "message": "Unknown session"}},
        status=404,
    )


def _upload_token(session_id: str) -> str:
    """Per-session capability token for chunk uploads.

    The MedScribe Alliance SDK uploads chunks S3-presigned-POST style:
    multipart form-data with NO Authorization header. The token rides in
    the upload form fields instead and is verified server-side.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"filly-upload:{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _upload_url_record(request: HttpRequest, session_id: str) -> dict:
    """upload_url payload for the SDK's 'aws' storage provider.

    ${filename} in the `key` field is replaced per-chunk by the SDK.
    """
    url = request.build_absolute_uri(f"/api/care_filly/v1/upload/{session_id}")
    return {
        "uploadData": {
            "url": url,
            "fields": {
                "key": "${filename}",
                "token": _upload_token(session_id),
            },
        }
    }


def _body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


@require_http_methods(["GET"])
def discovery(request: HttpRequest) -> JsonResponse:
    base = request.build_absolute_uri("/api/care_filly/v1").rstrip("/")
    return JsonResponse(
        {
            "protocol": "medscribealliance",
            "protocol_version": "0.1",
            "supported_versions": ["0.1"],
            "service": {
                "name": "CARE Filly Backend Plugin",
                "documentation_url": "https://github.com/ohcnetwork/care_filly",
            },
            "endpoints": {"base_url": base},
            "authentication": {"supported_methods": ["api_key"]},
            "capabilities": {
                "audio_formats": ["audio/mp3", "audio/mpeg", "audio/wav"],
                "max_chunk_duration_seconds": 20,
                "upload_methods": ["chunked", "single"],
                "storage_provider": "aws",
                "webhook_delivery": False,
                "client_sdk_delivery": True,
            },
            "models": [
                {
                    "id": "pro",
                    "display_name": "Whisper large-v3-turbo + LLM extraction",
                    "languages": SUPPORTED_LANGUAGES,
                    "max_session_duration_seconds": 3600,
                    "response_speed": "fast",
                    "features": {
                        "realtime_transcription": True,
                        "speaker_diarization": False,
                        "custom_templates": True,
                    },
                }
            ],
            "languages": {"supported": SUPPORTED_LANGUAGES, "auto_detection": True},
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def create_session(request: HttpRequest) -> JsonResponse:
    err, user = _authenticate(request)
    if err:
        return err
    body = _body(request)

    additional_data = body.get("additional_data") or {}
    facility_id = body.get("facility_id") or additional_data.get("facility_id")

    # Quota enforcement only applies to CARE-authenticated users; static
    # token / standalone mode has no user (and no database) to bill.
    if user is not None:
        if quota_error := quota.check_can_scribe(user, facility_id):
            return JsonResponse({"error": quota_error}, status=403)

    session_id = body.get("session_id") or uuid.uuid4().hex
    session = store.create_session(
        session_id,
        templates=body.get("templates") or [],
        language_hint=body.get("language_hint") or [],
        additional_data=additional_data,
        patient_details=body.get("patient_details"),
        user_id=getattr(user, "id", None),
        facility_id=str(facility_id) if facility_id else None,
    )
    logger.info("session %s created (templates=%s)", session_id, session["templates"])
    return JsonResponse(
        {
            "session_id": session_id,
            "status": session["status"],
            "created_at": session["created_at"],
            "expires_at": session["expires_at"],
            "upload_url": _upload_url_record(request, session_id),
            "patient_details": session["patient_details"],
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def upload_chunk(request: HttpRequest, session_id: str, filename: str) -> JsonResponse:
    """Legacy raw-body upload (Bearer auth, filename in path)."""
    if err := _auth_error(request):
        return err
    session = store.get_session(session_id)
    if session is None:
        return _not_found()
    return _accept_chunk(session, session_id, filename, request.body)


@csrf_exempt
@require_http_methods(["POST"])
def upload_chunk_multipart(request: HttpRequest, session_id: str) -> JsonResponse:
    """MedScribe Alliance SDK upload: multipart POST, token in form fields."""
    session = store.get_session(session_id)
    if session is None:
        return _not_found()

    token = request.POST.get("token", "")
    if not hmac.compare_digest(token, _upload_token(session_id)):
        return JsonResponse(
            {"error": {"code": "unauthorized", "message": "Invalid upload token"}},
            status=401,
        )

    file = request.FILES.get("file")
    filename = request.POST.get("key") or (file.name if file else "")
    audio = file.read() if file else b""
    return _accept_chunk(session, session_id, filename, audio)


def _accept_chunk(
    session: dict, session_id: str, filename: str, audio: bytes
) -> JsonResponse:
    if not audio:
        return JsonResponse({"error": "Empty audio body"}, status=400)
    if not filename:
        return JsonResponse({"error": "Missing filename"}, status=400)

    idx = store.parse_chunk_index(filename)
    if idx is None:
        idx = len(session["chunk_indexes"])
    store.register_chunk(session_id, idx, filename)

    language = engine.resolve_language(session.get("language_hint") or [])

    def _transcribe() -> None:
        try:
            text = engine.transcribe_chunk(audio, filename, language)
            store.set_chunk_result(session_id, idx, text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("chunk %s transcription failed", filename)
            store.set_chunk_result(session_id, idx, "", error=str(exc))
            with store.session_lock(session_id):
                s = store.get_session(session_id)
                if s is not None:
                    s["processing_errors"].append(
                        {
                            "type": "transcription_error",
                            "message": str(exc),
                            "file": filename,
                        }
                    )
                    store.save_session(s)

    # Transcription starts NOW, while the doctor is still talking.
    threading.Thread(target=_transcribe, daemon=True).start()

    return JsonResponse({"success": True, "file": filename})


def _finalize(session_id: str) -> None:
    """Wait for chunk transcripts, assemble, then run LLM extraction."""
    # Runs on a daemon thread — discard stale DB connections before ORM use.
    from django.db import close_old_connections

    close_old_connections()
    session = store.get_session(session_id)
    if session is None:
        return
    indexes = session["chunk_indexes"]

    deadline = time.monotonic() + FINALIZE_TIMEOUT_SECONDS
    chunks = store.get_chunks(session_id, indexes)
    while not store.all_chunks_done(chunks, indexes) and time.monotonic() < deadline:
        time.sleep(0.25)
        chunks = store.get_chunks(session_id, indexes)

    transcript = store.assemble_transcript(chunks)
    store.update_session(session_id, transcript=transcript)
    logger.info("session %s transcript ready (%d chars)", session_id, len(transcript))

    care_template = (session.get("additional_data") or {}).get("care_template") or {}
    template_ids = session.get("templates") or ["care_form"]

    llm_usage = None
    try:
        if transcript:
            data, llm_usage = engine.extract_structured(
                transcript,
                care_template.get("desc"),
                care_template.get("example"),
            )
        else:
            data = {}
        results = {tid: {"status": "success", "data": data} for tid in template_ids}
        status = "completed"
        errors = []
    except Exception as exc:  # noqa: BLE001
        logger.exception("session %s extraction failed", session_id)
        results = {
            tid: {
                "status": "failure",
                "error": {"code": "extraction_failed", "message": str(exc)},
            }
            for tid in template_ids
        }
        status = "partial" if transcript else "failed"
        errors = [{"type": "extraction_error", "message": str(exc)}]

    usage_summary = quota.record_usage(session, llm_usage)

    from .history_views import record_history

    record_history(
        session,
        status,
        transcript,
        data if status == "completed" else None,
        error=errors[0]["message"] if errors else None,
    )

    with store.session_lock(session_id):
        s = store.get_session(session_id)
        if s is None:
            return
        s["template_results"] = results
        s["status"] = status
        s["completed_at"] = store.now_iso()
        s["processing_errors"].extend(errors)
        if usage_summary:
            s["usage"] = usage_summary
        store.save_session(s)
    logger.info("session %s finalized: %s", session_id, status)


@csrf_exempt
@require_http_methods(["POST"])
def end_session(request: HttpRequest, session_id: str) -> JsonResponse:
    if err := _auth_error(request):
        return err
    session = store.update_session(session_id, status="processing")
    if session is None:
        return _not_found()

    threading.Thread(target=_finalize, args=(session_id,), daemon=True).start()

    return JsonResponse(
        {
            "session_id": session_id,
            "status": "processing",
            "message": "Processing started",
            "audio_files_received": len(session["audio_files"]),
            "audio_files": session["audio_files"],
        }
    )


@csrf_exempt
def session_detail(request: HttpRequest, session_id: str) -> HttpResponse:
    if request.method == "PATCH":
        return _patch_session(request, session_id)
    if request.method == "GET":
        return _get_session_status(request, session_id)
    return HttpResponse(status=405)


def _get_session_status(request: HttpRequest, session_id: str) -> JsonResponse:
    if err := _auth_error(request):
        return err
    session = store.get_session(session_id)
    if session is None:
        return _not_found()

    template_results = session["template_results"]
    if template_results:
        templates = [{tid: result} for tid, result in template_results.items()]
    elif session["status"] == "processing":
        templates = [{tid: {"status": "in-progress"}} for tid in session["templates"]]
    else:
        templates = []

    # Expose the transcript as soon as all chunks are transcribed — the FE
    # shows it immediately while extraction is still running.
    transcript = session["transcript"]
    chunks = store.get_chunks(session_id, session["chunk_indexes"])
    if (
        transcript is None
        and chunks
        and store.all_chunks_done(chunks, session["chunk_indexes"])
    ):
        transcript = store.assemble_transcript(chunks)

    return JsonResponse(
        {
            "session_id": session_id,
            "status": session["status"],
            "created_at": session["created_at"],
            "expires_at": session["expires_at"],
            "completed_at": session["completed_at"],
            "model_used": "pro",
            "language_detected": None,
            "audio_files_received": len(session["audio_files"]),
            "audio_files": session["audio_files"],
            "audio_files_processed": sum(
                1 for c in chunks.values() if c.get("text") is not None
            ),
            "additional_data": session["additional_data"],
            "templates": templates,
            "transcript": transcript,
            "processing_errors": session["processing_errors"],
            "patient_details": session["patient_details"],
            "usage": session.get("usage"),
            "upload_url": _upload_url_record(request, session_id),
        }
    )


def _patch_session(request: HttpRequest, session_id: str) -> JsonResponse:
    if err := _auth_error(request):
        return err
    body = _body(request)
    updates = {}
    for key in ("templates", "language_hint", "patient_details"):
        if body.get(key):
            updates[key] = body[key]
    session = store.get_session(session_id)
    if session is None:
        return _not_found()
    if body.get("additional_data"):
        merged = {**session["additional_data"], **body["additional_data"]}
        updates["additional_data"] = merged
    if updates:
        store.update_session(session_id, **updates)
    return JsonResponse(
        {
            "session_id": session_id,
            "status": session["status"],
            "message": "Session updated",
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def process_template(
    request: HttpRequest, session_id: str, template_id: str
) -> JsonResponse:
    if err := _auth_error(request):
        return err
    session = store.get_session(session_id)
    if session is None:
        return _not_found()

    with store.session_lock(session_id):
        s = store.get_session(session_id)
        if template_id not in s["templates"]:
            s["templates"].append(template_id)
        s["template_results"].pop(template_id, None)
        s["status"] = "processing"
        store.save_session(s)

    threading.Thread(target=_finalize, args=(session_id,), daemon=True).start()
    return JsonResponse(
        {
            "session_id": session_id,
            "template_id": template_id,
            "status": "processing",
            "message": "Template processing triggered",
        }
    )


@require_http_methods(["GET"])
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "mock": mock_mode()})
