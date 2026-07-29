"""Pydantic specs (CARE EMRResource pattern) for FillyQuota."""

from __future__ import annotations

from pydantic import UUID4

from care.emr.resources.base import EMRResource
from care_filly.models import FillyQuota, used_tokens


def user_dict(user) -> dict | None:
    if user is None:
        return None
    return {
        "username": user.username,
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
    }


def facility_name(facility_uuid) -> str | None:
    from care.facility.models.facility import Facility

    facility = Facility.objects.filter(external_id=facility_uuid).first()
    return getattr(facility, "name", None)


class FillyQuotaCreateSpec(EMRResource):
    __model__ = FillyQuota
    __exclude__ = []

    facility_external_id: UUID4
    tokens: int = 0
    tokens_per_user: int = 0
    allow_scribe: bool = True


class FillyQuotaUpdateSpec(EMRResource):
    __model__ = FillyQuota
    __exclude__ = []

    tokens: int | None = None
    tokens_per_user: int | None = None
    allow_scribe: bool | None = None


class FillyQuotaReadSpec(EMRResource):
    """Serialized exactly as the scribe frontend's ``ScribeQuota`` shape."""

    __model__ = FillyQuota
    __exclude__ = []

    external_id: str | None = None
    user: dict | None = None
    facility_external_id: str | None = None
    facility_name: str | None = None
    tokens: int = 0
    tokens_per_user: int = 0
    allow_scribe: bool = True
    used: int = 0
    tnc_accepted_date: str | None = None
    created_by: dict | None = None
    created_date: str | None = None
    modified_date: str | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        mapping["external_id"] = str(obj.external_id)
        mapping["user"] = user_dict(obj.user)
        mapping["facility_external_id"] = str(obj.facility_external_id)
        mapping["facility_name"] = kwargs.get("facility_name") or facility_name(
            obj.facility_external_id
        )
        mapping["used"] = used_tokens(obj.facility_external_id, obj.user_id)
        mapping["tnc_accepted_date"] = (
            obj.tnc_accepted_date.isoformat() if obj.tnc_accepted_date else None
        )
        mapping["created_by"] = user_dict(obj.created_by)
        mapping["created_date"] = (
            obj.created_date.isoformat() if obj.created_date else None
        )
        mapping["modified_date"] = (
            obj.modified_date.isoformat() if obj.modified_date else None
        )
