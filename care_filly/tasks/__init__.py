from celery import Celery, current_app

from care_filly.tasks.expire import expire_stale_sessions
from care_filly.tasks.finalize import finalize_session
from care_filly.tasks.transcribe import transcribe_chunk

__all__ = [
    "expire_stale_sessions",
    "finalize_session",
    "transcribe_chunk",
]


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs) -> None:
    sender.add_periodic_task(
        15 * 60,  # every 15 minutes
        expire_stale_sessions.s(),
        name="care_filly_expire_stale_sessions",
    )
