"""Pydantic spec (CARE EMRResource pattern) for FillyHistory."""

from __future__ import annotations

from care.emr.resources.base import EMRResource
from care_filly.models import FillyHistory


class FillyHistoryReadSpec(EMRResource):
    """Serialized exactly as the scribe frontend's ``HistoryEntryDto`` shape."""

    __model__ = FillyHistory
    __exclude__ = []

    id: str | None = None
    session_id: str | None = None
    facility_external_id: str | None = None
    started_at: str | None = None
    duration_seconds: int = 0
    status: str | None = None
    transcript: str | None = None
    structured_data: dict | None = None
    error: str | None = None
    has_audio: bool = False
    audio_mime_type: str | None = None

    @classmethod
    def perform_extra_serialization(cls, mapping, obj, *args, **kwargs):
        mapping["id"] = str(obj.external_id)
        mapping["facility_external_id"] = (
            str(obj.facility_external_id) if obj.facility_external_id else None
        )
        mapping["started_at"] = obj.started_at.isoformat()
        mapping["has_audio"] = obj.has_audio()
