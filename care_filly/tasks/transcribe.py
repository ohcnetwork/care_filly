"""Per-chunk transcription task.

Transcription starts as soon as a chunk is uploaded — while the doctor is
still speaking — so the transcript is ready almost immediately when the
session ends. Runs on Celery workers instead of the old daemon threads.
"""

import logging

from celery import shared_task

from care_filly import chunk_store
from care_filly.providers import (
    ProviderError,
    TransientProviderError,
    get_asr_provider,
)
from care_filly.tasks.utils import record_session_error

logger = logging.getLogger("care_filly")


@shared_task(bind=True, max_retries=3)
def transcribe_chunk(
    self, session_id: str, idx: int, filename: str, language: str | None
) -> None:
    audio = chunk_store.get_chunk_audio(session_id, idx)
    if not audio:
        chunk_store.set_chunk_result(session_id, idx, "", error="Audio chunk missing")
        return

    try:
        text = get_asr_provider().transcribe(audio, filename, language)
    except TransientProviderError as exc:
        try:
            raise self.retry(exc=exc, countdown=2)
        except self.MaxRetriesExceededError:
            logger.exception("chunk %s transcription gave up", filename)
            chunk_store.set_chunk_result(session_id, idx, "", error=str(exc))
            record_session_error(session_id, "transcription_error", str(exc), filename)
            return
    except ProviderError as exc:
        logger.exception("chunk %s transcription failed", filename)
        chunk_store.set_chunk_result(session_id, idx, "", error=str(exc))
        record_session_error(session_id, "transcription_error", str(exc), filename)
        return

    chunk_store.set_chunk_result(session_id, idx, text)
