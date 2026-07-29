"""Shared helpers for filly Celery tasks."""

import logging

from care_filly import chunk_store
from care_filly.models import FillySession

logger = logging.getLogger("care_filly")


def record_session_error(
    session_id: str, error_type: str, message: str, file: str | None = None
) -> None:
    """Append a processing error onto a session row (best-effort)."""
    with chunk_store.session_lock(session_id):
        session = FillySession.objects.filter(external_id=session_id).first()
        if session is None:
            return
        entry = {"type": error_type, "message": message}
        if file is not None:
            entry["file"] = file
        session.processing_errors = (session.processing_errors or []) + [entry]
        session.save(update_fields=["processing_errors", "modified_date"])
