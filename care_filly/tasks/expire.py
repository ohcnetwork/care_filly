"""Expire stale filly sessions that never finished recording/processing."""

import logging

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from care_filly.models import FillySession, SessionStatus

logger = logging.getLogger("care_filly")

# Sessions in a non-terminal state can be expired once past expires_at.
_NON_TERMINAL = (
    SessionStatus.CREATED,
    SessionStatus.RECORDING,
    SessionStatus.PROCESSING,
)


@shared_task
def expire_stale_sessions() -> int:
    now = timezone.now()
    stale = FillySession.objects.filter(
        Q(status__in=_NON_TERMINAL) & Q(expires_at__lt=now)
    )
    count = stale.update(status=SessionStatus.EXPIRED, modified_date=now)
    if count:
        logger.info("expired %d stale filly sessions", count)
    return count
