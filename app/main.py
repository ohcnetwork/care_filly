"""FastAPI app implementing the MedScribe Alliance Protocol (v0.1).

Drop-in backend for med-scribe-alliance-ts-sdk — point the SDK's
allianceConfig.baseUrl at {PUBLIC_BASE_URL}/v1.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import uuid
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import asr, config, extraction
from .store import Session, now_iso, parse_chunk_index, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("filly")

app = FastAPI(title="care-filly", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_auth(authorization: Optional[str]) -> None:
    if not config.FILLY_AUTH_TOKEN:
        return
    expected = f"Bearer {config.FILLY_AUTH_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


def _get_session(session_id: str) -> Session:
    session = store.get(session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "session_not_found", "message": "Unknown session"},
        )
    return session


def _upload_token(session_id: str) -> str:
    """Per-session capability token — SDK uploads carry no Authorization header."""
    secret = config.FILLY_AUTH_TOKEN or "care-filly"
    return hmac.new(
        secret.encode(), f"filly-upload:{session_id}".encode(), hashlib.sha256
    ).hexdigest()


def _upload_url_record(session_id: str) -> dict[str, Any]:
    """upload_url payload for the SDK's 'aws' storage provider."""
    return {
        "uploadData": {
            "url": f"{config.PUBLIC_BASE_URL}/v1/upload/{session_id}",
            "fields": {
                "key": "${filename}",
                "token": _upload_token(session_id),
            },
        }
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = [
    "auto_detect", "en", "en-IN", "en-US", "hi", "gu", "kn", "ml", "ta",
    "te", "bn", "mr", "pa", "ur", "es", "fr", "de", "pt", "ar",
]


@app.get("/v1/.well-known/medscribealliance")
async def discovery() -> dict[str, Any]:
    base = f"{config.PUBLIC_BASE_URL}/v1"
    return {
        "protocol": "medscribealliance",
        "protocol_version": "0.1",
        "supported_versions": ["0.1"],
        "service": {
            "name": "CARE Filly Backend",
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


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@app.post("/v1/sessions")
async def create_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)
    body = await request.json()

    session_id = body.get("session_id") or uuid.uuid4().hex
    session = store.create(
        session_id,
        templates=body.get("templates") or [],
        language_hint=body.get("language_hint") or [],
        additional_data=body.get("additional_data") or {},
        patient_details=body.get("patient_details"),
    )
    session.status = "created"
    logger.info(
        "session %s created (templates=%s, lang=%s)",
        session_id,
        session.templates,
        session.language_hint,
    )
    return {
        "session_id": session.session_id,
        "status": session.status,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "upload_url": _upload_url_record(session.session_id),
        "patient_details": session.patient_details,
    }


@app.post("/v1/upload/{session_id}")
async def upload_chunk_multipart(
    session_id: str,
    file: UploadFile = File(...),
    key: str = Form(default=""),
    token: str = Form(default=""),
) -> dict[str, Any]:
    """MedScribe Alliance SDK upload: multipart POST, token in form fields."""
    session = _get_session(session_id)
    if not hmac.compare_digest(token, _upload_token(session_id)):
        raise HTTPException(status_code=401, detail="Invalid upload token")

    audio = await file.read()
    filename = key or file.filename or ""
    return _accept_chunk(session, filename, audio)


@app.post("/v1/upload/{session_id}/{filename}")
async def upload_chunk(
    session_id: str,
    filename: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Legacy raw-body upload (Bearer auth, filename in path)."""
    _check_auth(authorization)
    session = _get_session(session_id)
    audio = await request.body()
    return _accept_chunk(session, filename, audio)


def _accept_chunk(session: Session, filename: str, audio: bytes) -> dict[str, Any]:
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio body")
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    chunk_index = parse_chunk_index(filename)
    if chunk_index is None:
        chunk_index = len(session.chunk_transcripts)

    session.status = "recording"
    session.audio_files.append(filename)
    session.chunk_transcripts[chunk_index] = None

    language = asr.resolve_language(session.language_hint)

    async def _transcribe() -> None:
        try:
            text = await asr.transcribe_chunk(audio, filename, language)
            session.chunk_transcripts[chunk_index] = text
        except Exception as exc:  # noqa: BLE001
            logger.exception("chunk %s transcription failed", filename)
            session.chunk_transcripts[chunk_index] = ""
            session.processing_errors.append(
                {
                    "type": "transcription_error",
                    "message": str(exc),
                    "file": filename,
                }
            )

    # Transcription starts NOW, while the doctor is still talking.
    session.chunk_tasks.append(asyncio.create_task(_transcribe()))

    return {"success": True, "file": filename}


async def _finalize(session: Session) -> None:
    """Assemble transcript, then run LLM extraction for each template."""
    if session.chunk_tasks:
        await asyncio.gather(*session.chunk_tasks, return_exceptions=True)

    session.transcript = session.assemble_transcript()
    logger.info(
        "session %s transcript ready (%d chars)",
        session.session_id,
        len(session.transcript),
    )

    care_template = session.additional_data.get("care_template") or {}
    template_desc = care_template.get("desc")
    template_example = care_template.get("example")

    try:
        if session.transcript:
            data = await extraction.extract_structured(
                session.transcript, template_desc, template_example
            )
        else:
            data = {}
        for template_id in session.templates or ["care_form"]:
            session.template_results[template_id] = {
                "status": "success",
                "data": data,
            }
        session.status = "completed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("session %s extraction failed", session.session_id)
        for template_id in session.templates or ["care_form"]:
            session.template_results[template_id] = {
                "status": "failure",
                "error": {"code": "extraction_failed", "message": str(exc)},
            }
        session.processing_errors.append(
            {"type": "extraction_error", "message": str(exc)}
        )
        # Transcript still usable -> partial
        session.status = "partial" if session.transcript else "failed"

    session.completed_at = now_iso()
    logger.info("session %s finalized: %s", session.session_id, session.status)


@app.post("/v1/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)
    session = _get_session(session_id)
    body = await request.json()

    sent = int(body.get("audio_files_sent") or 0)
    received = len(session.audio_files)
    if sent and received < sent:
        logger.warning(
            "session %s: client sent %d files but server received %d",
            session_id,
            sent,
            received,
        )

    session.status = "processing"
    if session.finalize_task is None or session.finalize_task.done():
        session.finalize_task = asyncio.create_task(_finalize(session))

    return {
        "session_id": session.session_id,
        "status": session.status,
        "message": "Processing started",
        "audio_files_received": received,
        "audio_files": session.audio_files,
    }


@app.get("/v1/sessions/{session_id}")
async def get_session_status(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)
    session = _get_session(session_id)

    templates = (
        [{tid: result} for tid, result in session.template_results.items()]
        if session.template_results
        else [
            {tid: {"status": "in-progress"}}
            for tid in session.templates
            if session.status == "processing"
        ]
    )

    # Expose the transcript as soon as all chunks are transcribed — the FE
    # shows it immediately while extraction is still running.
    transcript = session.transcript
    if (
        transcript is None
        and session.chunk_transcripts
        and session.all_chunks_transcribed()
    ):
        transcript = session.assemble_transcript()

    return {
        "session_id": session.session_id,
        "status": session.status,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "completed_at": session.completed_at,
        "model_used": "pro",
        "language_detected": session.language_detected,
        "audio_files_received": len(session.audio_files),
        "audio_files": session.audio_files,
        "audio_files_processed": sum(
            1 for t in session.chunk_transcripts.values() if t is not None
        ),
        "additional_data": session.additional_data,
        "templates": templates,
        "transcript": transcript,
        "processing_errors": session.processing_errors,
        "patient_details": session.patient_details,
        "upload_url": _upload_url_record(session.session_id),
    }


@app.patch("/v1/sessions/{session_id}")
async def patch_session(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)
    session = _get_session(session_id)
    body = await request.json()

    if body.get("templates"):
        session.templates = body["templates"]
    if body.get("language_hint"):
        session.language_hint = body["language_hint"]
    if body.get("additional_data"):
        session.additional_data.update(body["additional_data"])
    if body.get("patient_details"):
        session.patient_details = body["patient_details"]

    return {
        "session_id": session.session_id,
        "status": session.status,
        "message": "Session updated",
    }


@app.post("/v1/sessions/{session_id}/process/template/{template_id}")
async def process_template(
    session_id: str,
    template_id: str,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _check_auth(authorization)
    session = _get_session(session_id)

    if template_id not in session.templates:
        session.templates.append(template_id)
    session.template_results.pop(template_id, None)
    session.status = "processing"
    session.finalize_task = asyncio.create_task(_finalize(session))

    return {
        "session_id": session.session_id,
        "template_id": template_id,
        "status": "processing",
        "message": "Template processing triggered",
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "mock": config.MOCK_MODE}
