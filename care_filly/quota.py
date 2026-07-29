"""Quota enforcement helpers.

Enforcement happens at session creation (unlike the reference
implementation, which fails only after audio upload) so the user gets
immediate, actionable feedback. Usage is recorded separately by the
finalize task (see ``care_filly.tasks.finalize``).
"""

import hashlib

from django.utils import timezone

from .models import FillyQuota, FillyUserPreference, used_tokens
from .settings import terms_and_conditions


def hash_string(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def current_tnc() -> tuple[str, str]:
    tnc = terms_and_conditions()
    return tnc, hash_string(tnc)


def check_can_filly(user, facility) -> dict | None:
    """Return an error dict {code, message} if the user may not use filly.

    ``facility`` is a resolved CARE ``Facility`` instance (or ``None``).
    """
    if facility is None:
        return {
            "code": "facility_required",
            "message": "A valid facility_id is required to start a filly session.",
        }

    facility_quota = FillyQuota.objects.filter(facility=facility, user=None).first()
    if facility_quota is None:
        return {
            "code": "no_facility_quota",
            "message": "Facility does not have a filly quota.",
        }
    if not facility_quota.allow_filly:
        return {
            "code": "filly_disabled",
            "message": "Filly is not enabled for this facility.",
        }

    _, tnc_hash = current_tnc()
    pref = FillyUserPreference.objects.filter(user=user).first()
    if pref is None or not pref.filly_enabled or pref.tnc_hash != tnc_hash:
        return {
            "code": "filly_not_enabled",
            "message": "Enable AI Filly from your profile (and accept the "
            "terms and conditions) to use filly.",
        }

    user_quota = FillyQuota.objects.filter(facility=facility, user=user).first()
    if user_quota is None:
        # Auto-provision the per-user quota row on first use.
        user_quota = FillyQuota.objects.create(
            user=user,
            facility=facility,
            tokens=facility_quota.tokens_per_user,
            tnc_hash=pref.tnc_hash,
            tnc_accepted_date=pref.tnc_accepted_date or timezone.now(),
        )
    if not user_quota.allow_filly:
        return {
            "code": "filly_disabled",
            "message": "Filly is not enabled for this user.",
        }

    if used_tokens(facility.id) >= facility_quota.tokens:
        return {
            "code": "facility_quota_exceeded",
            "message": "Facility has exceeded its monthly filly quota.",
        }
    if used_tokens(facility.id, user.id) >= user_quota.tokens:
        return {
            "code": "user_quota_exceeded",
            "message": "You have exceeded your monthly filly quota.",
        }
    return None
