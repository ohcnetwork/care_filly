"""Celery tasks for care_filly.

``finalize_session`` is the only long-running operation that belongs on
a worker: it polls for per-chunk transcripts (up to 90 s), assembles the
transcript, makes an LLM call, writes DB rows (usage, history), and
updates the Redis session state. Running it in a daemon thread inside
gunicorn means it dies silently on worker restart and leaves sessions
stuck in "processing" permanently.

Chunk transcription (``_accept_chunk`` → thread) stays as a thread
because it is latency-critical (starts while the doctor is still
talking) and holds the raw audio bytes in-process — routing ~1 MB audio
through the broker per chunk is not worth it.

CELERY_TASK_ALWAYS_EAGER (set in test/standalone environments) makes
``finalize_session.delay()`` execute synchronously in the caller's
process, so tests work without a live worker.
"""

from __future__ import annotations

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

# Celery's soft_time_limit triggers SoftTimeLimitExceeded (catchable);
# time_limit is the hard kill. Both are set to outlast the finalize
# polling window + the LLM call.
_SOFT_TL = 120
_HARD_TL = 150


@shared_task(
    bind=True,
    max_retries=0,
    soft_time_limit=_SOFT_TL,
    time_limit=_HARD_TL,
    queue="celery",
)
def finalize_session(self, session_id: str) -> None:
    """Assemble transcript and run LLM extraction for a finished session.

    Imported inline to avoid circular imports at module load time.
    """
    from care_filly.api.viewsets.scribe import _finalize

    logger.info("finalize_session started: %s", session_id)
    _finalize(session_id)
    logger.info("finalize_session done: %s", session_id)
