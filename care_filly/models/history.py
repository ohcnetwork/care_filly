from __future__ import annotations

import time
import uuid

from django.conf import settings
from django.db import models

from care.emr.models.base import EMRBaseModel
from care.emr.utils.file_manager import S3FilesManager
from care.utils.csp.config import BucketType


class FillyHistory(EMRBaseModel):
    """One row per finished scribe session, owned by the recording user.

    Written server-side at finalize time so history survives across
    browsers/devices and is never visible to other users. The client
    uploads its locally captured recording right after the session
    completes (the transcription pipeline itself discards audio).

    Built on ``EMRBaseModel`` so this gets the same auditing CARE's own
    clinical models use for free: ``external_id``, ``created_date``,
    ``modified_date``, ``created_by``/``updated_by`` and soft `delete()`
    (``deleted=True``, row kept) via the default manager.

    Recordings are never written to local disk. They live in CARE's
    S3-compatible object storage (Minio locally, a real bucket in
    production) via the same ``S3FilesManager`` the core ``FileUpload``
    model uses — see ``care.utils.csp.config`` for how the bucket/
    credentials are resolved from settings. There is no local-storage
    fallback: a bucket must be configured for uploads/downloads to work.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filly_history",
    )
    facility_external_id = models.UUIDField(db_index=True, null=True, blank=True)
    session_id = models.CharField(max_length=64, db_index=True)
    started_at = models.DateTimeField(help_text="When the recording started")
    duration_seconds = models.IntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=[("completed", "Completed"), ("failed", "Failed")],
    )
    transcript = models.TextField(null=True, blank=True)
    structured_data = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    # Object-storage key for the uploaded recording (see S3FilesManager).
    # Random and never exposed to the client — only signed URLs are.
    internal_name = models.CharField(max_length=255, null=True, blank=True)
    audio_mime_type = models.CharField(max_length=64, null=True, blank=True)

    # Recordings contain transcribed clinical audio, so they're stored in
    # the same bucket as other patient files rather than a plugin-only one.
    files_manager = S3FilesManager(BucketType.PATIENT)
    file_type = "filly_history"

    class Meta:
        verbose_name = "Filly History"
        verbose_name_plural = "Filly Histories"
        ordering = ["-started_at"]

    @property
    def name(self) -> str:
        return f"{self.session_id}-recording"

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

        Unlike the inherited `delete()` (soft delete — row and audio kept
        for audit), this is for admin/data-retention purges only.
        """
        if self.internal_name:
            self.files_manager.delete_object(self, quiet=True)
        models.Model.delete(self)

    def __str__(self) -> str:
        return f"{self.user_id} - {self.session_id} ({self.status})"
