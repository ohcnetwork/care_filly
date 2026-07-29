"""Per-user scribe opt-in, set from the user's profile page."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from care.emr.models.base import EMRBaseModel


class FillyUserPreference(EMRBaseModel):
    """Per-user opt-in for scribe, set from the user's profile page.

    Scribe UI is hidden until the user enables it; enabling requires
    accepting the current terms & conditions (hash stamped here). This is
    global per user — facility-level availability/quota still applies.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filly_preference",
    )
    scribe_enabled = models.BooleanField(default=False)
    tnc_hash = models.CharField(max_length=255, null=True, blank=True)
    tnc_accepted_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Filly User Preference"
        verbose_name_plural = "Filly User Preferences"

    def __str__(self) -> str:
        return f"{self.user_id} - {'enabled' if self.scribe_enabled else 'disabled'}"
