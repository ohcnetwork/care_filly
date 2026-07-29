"""Session finalization task.

Replaces the old busy-wait daemon thread: instead of sleeping until the
chunk transcripts land, the task re-queues itself with a short countdown
(``self.retry``) until every chunk is transcribed or the finalize budget
runs out. It then assembles the transcript, runs LLM extraction, records
a usage row and writes the terminal session state in one transaction.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from care_filly import chunk_store
from care_filly.models import FillySession, FillyUsage, SessionStatus
from care_filly.providers import ProviderError, get_llm_provider
from care_filly.settings import plugin_settings

logger = logging.getLogger("care_filly")


def _retry_budget() -> int:
    # One retry every 2s until the finalize timeout is spent.
    return max(1, plugin_settings.FINALIZE_TIMEOUT_SECONDS // 2)


@shared_task(bind=True)
def finalize_session(self, session_id: str) -> None:
    session = FillySession.objects.filter(external_id=session_id).first()
    if session is None:
        return

    indexes = session.chunk_indexes or []
    chunks = chunk_store.get_chunks(session_id, indexes)

    if indexes and not chunk_store.all_chunks_done(chunks, indexes):
        if session.finalize_attempts < _retry_budget():
            FillySession.objects.filter(pk=session.pk).update(
                finalize_attempts=session.finalize_attempts + 1
            )
            raise self.retry(countdown=2, max_retries=_retry_budget())
        logger.warning("session %s finalize timed out waiting for chunks", session_id)

    transcript = chunk_store.assemble_transcript(chunks)
    logger.info("session %s transcript ready (%d chars)", session_id, len(transcript))

    care_template = (session.additional_data or {}).get("care_template") or {}
    template_ids = session.templates or ["care_form"]

    llm_usage = None
    errors: list[dict] = []
    try:
        if transcript:
            data, llm_usage = get_llm_provider().extract(
                transcript,
                care_template.get("desc"),
                care_template.get("example"),
            )
        else:
            data = {}
        results = {tid: {"status": "success", "data": data} for tid in template_ids}
        status = SessionStatus.COMPLETED
    except ProviderError as exc:
        logger.exception("session %s extraction failed", session_id)
        results = {
            tid: {
                "status": "failure",
                "error": {"code": "extraction_failed", "message": str(exc)},
            }
            for tid in template_ids
        }
        status = SessionStatus.PARTIAL if transcript else SessionStatus.FAILED
        errors = [{"type": "extraction_error", "message": str(exc)}]

    duration_seconds = plugin_settings.CHUNK_SECONDS * len(indexes)
    usage_summary = _record_usage(session, llm_usage, duration_seconds)

    with transaction.atomic():
        row = FillySession.objects.select_for_update().get(pk=session.pk)
        row.template_results = results
        row.transcript = transcript or None
        row.status = status
        row.completed_at = timezone.now()
        row.duration_seconds = duration_seconds
        row.processing_errors = (row.processing_errors or []) + errors
        if usage_summary:
            row.usage = usage_summary
        row.save(
            update_fields=[
                "template_results",
                "transcript",
                "status",
                "completed_at",
                "duration_seconds",
                "processing_errors",
                "usage",
                "modified_date",
            ]
        )
    logger.info("session %s finalized: %s", session_id, status)


def _record_usage(session, llm_usage, duration_seconds) -> dict | None:
    """Persist one immutable usage row; must never break finalize."""
    input_tokens = int((llm_usage or {}).get("prompt_tokens") or 0)
    output_tokens = int((llm_usage or {}).get("completion_tokens") or 0)
    try:
        row = FillyUsage.objects.create(
            session=session,
            user=session.user,
            facility=session.facility,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_seconds=duration_seconds,
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to record usage for session %s", session.external_id)
        return None
    return {
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "audio_seconds": row.audio_seconds,
    }
