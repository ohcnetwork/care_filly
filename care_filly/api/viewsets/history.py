"""User-scoped scribe history (CARE EMR viewset pattern).

History rows are written server-side when a session finalizes, so a
user's past sessions follow them across browsers and devices. Every
endpoint operates strictly on ``request.user``'s own rows — no user can
list or delete another user's history.

- GET    v1/history                          -> {count, results} (newest first)
- DELETE v1/history                          -> clear the user's entire history
- DELETE v1/history/<external_id>            -> delete one entry
- GET    v1/history/<external_id>/audio      -> redirect to a signed recording URL
- POST   v1/history/session/<session_id>/audio -> attach the client's recording
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from care.emr.api.viewsets.base import (
    EMRBaseViewSet,
    EMRDestroyMixin,
    EMRListMixin,
)
from care.utils.pagination.care_pagination import CareLimitOffsetPagination
from care_filly.api.exceptions import FillyAPIError, filly_exception_handler
from care_filly.models import FillyHistory
from care_filly.quota import parse_facility_id
from care_filly.resources.history import FillyHistoryReadSpec

logger = logging.getLogger("care_filly")

MAX_AUDIO_BYTES = 100 * 1024 * 1024  # 100 MB


def record_history(
    session: dict,
    status: str,
    transcript: str,
    structured_data: dict | None,
    error: str | None = None,
) -> None:
    """Persist a FillyHistory row for a finalized session.

    Best-effort: history must never break finalize. Skipped in
    standalone/static-token mode where there is no CARE user to own it.
    """
    user_id = session.get("user_id")
    if user_id is None:
        return

    started_at = timezone.now()
    try:
        started_at = datetime.fromisoformat(session["created_at"])
    except (KeyError, ValueError, TypeError):
        pass

    try:
        FillyHistory.objects.create(
            user_id=user_id,
            facility_external_id=parse_facility_id(session.get("facility_id")),
            session_id=session["session_id"],
            started_at=started_at,
            duration_seconds=20 * len(session.get("chunk_indexes") or []),
            status="completed" if status == "completed" else "failed",
            transcript=transcript or None,
            structured_data=structured_data or None,
            error=error,
        )
    except Exception:
        logger.exception(
            "failed to record history for session %s", session["session_id"]
        )


class FillyHistoryPagination(CareLimitOffsetPagination):
    default_limit = 50
    max_limit = 100


class FillyHistoryViewSet(EMRListMixin, EMRDestroyMixin, EMRBaseViewSet):
    database_model = FillyHistory
    pydantic_read_model = FillyHistoryReadSpec
    pagination_class = FillyHistoryPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_exception_handler(self):
        return filly_exception_handler

    def get_queryset(self):
        # Strictly the requesting user's own rows; soft-deleted rows are
        # already excluded by the default manager.
        return FillyHistory.objects.filter(user=self.request.user).order_by(
            "-started_at"
        )

    def get_object(self):
        entry_uuid = parse_facility_id(self.kwargs[self.lookup_field])
        entry = (
            self.get_queryset().filter(external_id=entry_uuid).first()
            if entry_uuid
            else None
        )
        if entry is None:
            raise FillyAPIError("not_found", "History entry not found", 404)
        return entry

    def clear(self, request):
        """Soft delete the user's entire history (rows/audio kept for audit)."""
        deleted = self.get_queryset().update(deleted=True, modified_date=timezone.now())
        return Response({"deleted": deleted})

    def audio(self, request, external_id: str):
        """Redirect to a short-lived signed URL for the user's own recording.

        The audio itself lives in object storage (Minio/S3) — the app
        server never proxies the bytes, same as CARE's own file uploads.
        """
        entry = self.get_object()
        if not entry.has_audio():
            raise FillyAPIError("not_found", "History entry not found", 404)
        return HttpResponseRedirect(entry.read_audio_url())

    def upload_audio(self, request, session_id: str):
        """Attach the client's locally captured recording to its history row.

        Keyed by session_id (the client never sees the row's external_id
        before listing). Only the row's owner can attach audio.
        """
        file = request.FILES.get("file")
        if file is None:
            raise FillyAPIError("missing_file", "No audio file")
        if file.size > MAX_AUDIO_BYTES:
            raise FillyAPIError("too_large", "Audio file too large", 413)

        entry = (
            self.get_queryset()
            .filter(session_id=session_id)
            .order_by("-created_date")
            .first()
        )
        if entry is None:
            raise FillyAPIError("not_found", "History entry not found", 404)

        mime = file.content_type or "audio/webm"
        entry.save_audio(file.read(), mime)  # uploads to object storage
        update_fields = ["internal_name", "audio_mime_type", "meta"]

        try:
            duration = int(float(request.data.get("duration", "")))
            if duration > 0:
                entry.duration_seconds = duration
                update_fields.append("duration_seconds")
        except ValueError:
            pass

        entry.save(update_fields=update_fields)
        return Response({"id": str(entry.external_id), "has_audio": True})
