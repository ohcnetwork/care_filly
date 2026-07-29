"""Persistent filly session model.

This single model is the source of truth for a filly session: it holds
the session/task state that used to live only in the Django cache *and*
absorbs the old ``FillyHistory`` (a finished session simply becomes a
history row). The per-chunk audio bytes and transcript slots still live
in the cache hot path (see ``care_filly.chunk_store``); everything
durable is here.

Built on ``EMRBaseModel`` so it inherits CARE's auditing for free:
``external_id`` (the durable filly session id),
``created_date``/``modified_date``, ``created_by``/``updated_by`` and a
soft ``delete()`` (used for "clear history").

Recordings live in CARE's S3-compatible object storage (Minio locally)
via the same ``S3FilesManager`` the core ``FileUpload`` model uses — there
is no local-disk fallback.
"""

import time
import uuid

from django.conf import settings
from django.db import models

from care.emr.models.base import EMRBaseModel
from care.emr.utils.file_manager import S3FilesManager
from care.utils.csp.config import BucketType


class SessionStatus(models.TextChoices):
    CREATED = "created", "Created"
    RECORDING = "recording", "Recording"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"


# Statuses that count as "history" (finished sessions the user can browse).
HISTORY_STATUSES = (
    SessionStatus.COMPLETED,
    SessionStatus.PARTIAL,
    SessionStatus.FAILED,
)

# Statuses in which chunk uploads are still accepted.
UPLOADABLE_STATUSES = (SessionStatus.CREATED, SessionStatus.RECORDING)


class FillySession(EMRBaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filly_sessions",
    )
    facility = models.ForeignKey(
        "facility.Facility",
        on_delete=models.CASCADE,
        related_name="filly_sessions",
    )
    status = models.CharField(
        max_length=16,
        choices=SessionStatus.choices,
        default=SessionStatus.CREATED,
        db_index=True,
    )

    # Request payload / SDK inputs.
    templates = models.JSONField(default=list, blank=True)
    language_hint = models.JSONField(default=list, blank=True)
    patient_details = models.JSONField(null=True, blank=True)
    additional_data = models.JSONField(default=dict, blank=True)

    # Chunk bookkeeping (audio + transcript slots live in the cache).
    chunk_indexes = models.JSONField(default=list, blank=True)
    audio_files = models.JSONField(default=list, blank=True)

    # Pipeline outputs.
    transcript = models.TextField(null=True, blank=True)
    template_results = models.JSONField(default=dict, blank=True)
    processing_errors = models.JSONField(default=list, blank=True)
    usage = models.JSONField(null=True, blank=True)

    # Celery finalize-task state (replaces the old daemon threads).
    finalize_task_id = models.CharField(max_length=255, null=True, blank=True)
    finalize_attempts = models.IntegerField(default=0)

    # Timing.
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    duration_seconds = models.IntegerField(default=0)

    # Object-storage key for the uploaded recording (never exposed to the
    # client — only signed URLs are). Random, opaque.
    internal_name = models.CharField(max_length=255, null=True, blank=True)
    audio_mime_type = models.CharField(max_length=64, null=True, blank=True)

    # Recordings contain transcribed clinical audio, so they're stored in
    # the same bucket as other patient files.
    files_manager = S3FilesManager(BucketType.PATIENT)
    file_type = "filly_session"

    class Meta:
        verbose_name = "Filly Session"
        verbose_name_plural = "Filly Sessions"
        ordering = ["-created_date"]

    def __str__(self) -> str:
        return f"{self.external_id} ({self.status})"

    # -- object-storage helpers (ported from the old FillyHistory) ---------

    @property
    def name(self) -> str:
        return f"{self.external_id}-recording"

    def get_extension(self) -> str:
        is_mp4 = self.audio_mime_type and "mp4" in self.audio_mime_type
        return ".m4a" if is_mp4 else ".webm"

    def has_audio(self) -> bool:
        return bool(self.internal_name)

    def save_audio(self, content: bytes, mime_type: str) -> None:
        """Upload a recording to object storage, replacing any existing one."""
        if self.internal_name:
            self.files_manager.delete_object(self, quiet=True)
        self.audio_mime_type = mime_type
        self.meta["mime_type"] = mime_type
        self.internal_name = f"{uuid.uuid4()}{int(time.time())}{self.get_extension()}"
        self.files_manager.put_object(self, content, ContentType=mime_type)

    def read_audio_url(self, duration: int = 60 * 60) -> str | None:
        """Short-lived signed URL clients fetch the recording from directly."""
        if not self.internal_name:
            return None
        return self.files_manager.read_signed_url(self, duration=duration)

    def purge(self) -> None:
        """Hard delete: permanently remove the row and its recording.

        Unlike the inherited soft ``delete()`` (row + audio kept for audit),
        this is for admin/data-retention purges only.
        """
        if self.internal_name:
            self.files_manager.delete_object(self, quiet=True)
        models.Model.delete(self)
