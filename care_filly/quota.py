"""Quota enforcement & usage recording helpers.

Enforcement happens at session creation (unlike the reference
implementation, which fails only after audio upload) so the user gets
immediate, actionable feedback.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from .models import FillyQuota, FillyUsage, FillyUserPreference, used_tokens
from .settings import plugin_settings

logger = logging.getLogger("care_filly")


def hash_string(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def current_tnc() -> tuple[str, str]:
    tnc = plugin_settings.FILLY_TNC
    return tnc, hash_string(tnc)


def parse_facility_id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def check_can_scribe(user, facility_id: Any) -> dict | None:  # noqa: PLR0911
    """Return an error dict {code, message} if the user may not scribe."""
    facility_uuid = parse_facility_id(facility_id)
    if facility_uuid is None:
        return {
            "code": "facility_required",
            "message": "A valid facility_id is required to start a scribe session.",
        }

    facility_quota = FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=None
    ).first()
    if facility_quota is None:
        return {
            "code": "no_facility_quota",
            "message": "Facility does not have a scribe quota.",
        }
    if not facility_quota.allow_scribe:
        return {
            "code": "scribe_disabled",
            "message": "Scribe is not enabled for this facility.",
        }

    user_quota = FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=user
    ).first()
    _, tnc_hash = current_tnc()
    pref = FillyUserPreference.objects.filter(user=user).first()
    if pref is None or not pref.scribe_enabled or pref.tnc_hash != tnc_hash:
        return {
            "code": "scribe_not_enabled",
            "message": "Enable AI Scribe from your profile (and accept the "
            "terms and conditions) to use scribe.",
        }
    if user_quota is None:
        # Auto-provision the per-user quota row on first use.
        user_quota = FillyQuota.objects.create(
            user=user,
            facility_external_id=facility_uuid,
            tokens=facility_quota.tokens_per_user,
            tnc_hash=pref.tnc_hash,
            tnc_accepted_date=pref.tnc_accepted_date,
        )
    if not user_quota.allow_scribe:
        return {
            "code": "scribe_disabled",
            "message": "Scribe is not enabled for this user.",
        }

    if used_tokens(facility_uuid) >= facility_quota.tokens:
        return {
            "code": "facility_quota_exceeded",
            "message": "Facility has exceeded its monthly scribe quota.",
        }
    if used_tokens(facility_uuid, user.id) >= user_quota.tokens:
        return {
            "code": "user_quota_exceeded",
            "message": "You have exceeded your monthly scribe quota.",
        }
    return None


def record_usage(session: dict, usage: dict | None) -> dict | None:
    """Persist a FillyUsage row for a finalized session.

    Returns the usage summary stored on the session (or None when there is
    nothing to record — e.g. standalone mode without a CARE user).
    """
    user_id = session.get("user_id")
    if user_id is None and session.get("facility_id") is None:
        return None

    input_tokens = int((usage or {}).get("prompt_tokens") or 0)
    output_tokens = int((usage or {}).get("completion_tokens") or 0)
    audio_seconds = 20 * len(session.get("chunk_indexes") or [])

    try:
        row = FillyUsage.objects.create(
            user_id=user_id,
            facility_external_id=parse_facility_id(session.get("facility_id")),
            session_id=session["session_id"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            audio_seconds=audio_seconds,
        )
    except Exception:
        logger.exception("failed to record usage for session %s", session["session_id"])
        return None
    return {
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.total_tokens,
        "audio_seconds": row.audio_seconds,
    }
