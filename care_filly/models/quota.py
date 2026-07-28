"""Quota & usage models (mirrors 10bedicu/care_scribe's ScribeQuota system).

Two kinds of FillyQuota rows per facility:
- facility-level (user=None): `tokens` is the facility's monthly pool and
  `tokens_per_user` is the allowance copied to each user row on TnC accept.
- user-level (user set): `tokens` is that user's monthly allowance.

Usage is NEVER stored as a counter on the quota. Each completed scribe
session writes one immutable FillyUsage row and monthly consumption is
aggregated live for the current calendar month (avoids the stale
month-boundary bug in the reference implementation).

Facilities are referenced by their CARE external UUID (no hard FK into
CARE internals) so the plugin stays loosely coupled.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


def month_window() -> tuple[datetime, datetime]:
    """[start, end) of the current calendar month, evaluated at call time."""
    now = timezone.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


class FillyQuota(models.Model):
    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_date = models.DateTimeField(auto_now=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filly_quotas",
        null=True,
        blank=True,
        help_text="Null for the facility-level quota row",
    )
    facility_external_id = models.UUIDField(db_index=True)
    tokens = models.IntegerField(
        default=0,
        help_text="Monthly token pool (facility row) or allowance (user row)",
    )
    tokens_per_user = models.IntegerField(
        default=0,
        help_text="Allowance copied to each user quota (facility rows only)",
    )
    allow_scribe = models.BooleanField(
        default=True,
        help_text="Whether scribe is enabled for this user/facility",
    )
    tnc_hash = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Hash of the terms and conditions accepted by the user",
    )
    tnc_accepted_date = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_filly_quotas",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Filly Quota"
        verbose_name_plural = "Filly Quotas"
        constraints = [
            models.UniqueConstraint(
                fields=["facility_external_id"],
                condition=Q(user__isnull=True),
                name="unique_facility_filly_quota",
            ),
            models.UniqueConstraint(
                fields=["user", "facility_external_id"],
                name="unique_user_facility_filly_quota",
            ),
        ]

    @property
    def used(self) -> int:
        """Tokens consumed in the current calendar month (computed live)."""
        return used_tokens(self.facility_external_id, self.user_id)

    def __str__(self) -> str:
        who = (
            self.user.username if self.user else f"facility:{self.facility_external_id}"
        )
        return f"{who} - {self.tokens} tokens"


class FillyUsage(models.Model):
    """One immutable row per completed scribe session."""

    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="filly_usages",
        null=True,
        blank=True,
    )
    facility_external_id = models.UUIDField(db_index=True, null=True, blank=True)
    session_id = models.CharField(max_length=64, db_index=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    audio_seconds = models.IntegerField(
        default=0, help_text="Estimated recorded audio duration"
    )

    class Meta:
        verbose_name = "Filly Usage"
        verbose_name_plural = "Filly Usages"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return f"{self.session_id}: {self.total_tokens} tokens"


def used_tokens(facility_external_id, user_id: Optional[int] = None) -> int:
    """Current-month token consumption for a facility or a user in it."""
    start, end = month_window()
    qs = FillyUsage.objects.filter(
        facility_external_id=facility_external_id,
        created_date__gte=start,
        created_date__lt=end,
    )
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    totals = qs.aggregate(
        total=Sum(models.F("input_tokens") + models.F("output_tokens"))
    )
    return totals["total"] or 0
