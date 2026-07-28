from __future__ import annotations

import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone


def history_audio_storage() -> FileSystemStorage:
    """Storage for history recordings.

    CARE's STORAGES setting defines no "default" backend, so the field
    needs an explicit one. Files land under MEDIA_ROOT.
    """
    return FileSystemStorage(location=settings.MEDIA_ROOT)


class FillyHistory(models.Model):
    """One row per finished scribe session, owned by the recording user.

    Written server-side at finalize time so history survives across
    browsers/devices and is never visible to other users. The client
    uploads its locally captured recording right after the session
    completes (the transcription pipeline itself discards audio).
    """

    external_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    created_date = models.DateTimeField(auto_now_add=True, db_index=True)

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
    audio_file = models.FileField(
        storage=history_audio_storage,
        upload_to="filly_history/%Y/%m/",
        null=True,
        blank=True,
        help_text="Recording uploaded by the client after the session ends",
    )
    audio_mime_type = models.CharField(max_length=64, null=True, blank=True)
    deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Soft-deleted entries are hidden from the user but kept "
        "(row + audio) for audit",
    )
    deleted_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Filly History"
        verbose_name_plural = "Filly Histories"
        ordering = ["-started_at"]

    def soft_delete(self) -> None:
        self.deleted = True
        self.deleted_date = timezone.now()
        self.save(update_fields=["deleted", "deleted_date"])

    def delete(self, *args, **kwargs):
        # Hard delete (admin/purge only) — remove the stored audio too.
        if self.audio_file:
            self.audio_file.delete(save=False)
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user_id} - {self.session_id} ({self.status})"
