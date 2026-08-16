"""Session/token seam for the Medispeak-hosted pipeline.

A thin pointer row: the transcript, structured data and usage all live on
Medispeak's side. It exists only so the token-mint endpoint can check the
requesting user owns the session.
"""

from django.conf import settings
from django.db import models

from care.emr.models.base import EMRBaseModel


class MedispeakSession(EMRBaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="medispeak_sessions",
    )
    facility = models.ForeignKey(
        "facility.Facility",
        on_delete=models.CASCADE,
        related_name="medispeak_sessions",
    )
    medispeak_session_id = models.CharField(max_length=64)

    class Meta:
        verbose_name = "Medispeak Session"
        verbose_name_plural = "Medispeak Sessions"
