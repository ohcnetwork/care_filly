"""Smoke test for user-scoped scribe history (run via manage.py shell)."""

from django.contrib.auth import get_user_model

from care_filly.api.viewsets.history import _history_dict, record_history
from care_filly.models import FillyHistory

user = get_user_model().objects.first()
session = {
    "session_id": "smoketest123",
    "user_id": user.id,
    "facility_id": None,
    "created_at": "2026-07-28T10:00:00+00:00",
    "chunk_indexes": [0, 1, 2],
}
record_history(session, "completed", "patient has fever", {"temp": "101F"}, None)
record_history(session, "failed", "", None, error="extraction blew up")

rows = FillyHistory.objects.filter(user=user, session_id="smoketest123").order_by(
    "-started_at"
)
print("rows:", rows.count())

# Attach + read back audio the way upload_history_audio does.
from django.core.files.base import ContentFile

entry = rows.filter(status="completed").first()
entry.audio_file.save(f"{entry.external_id}.webm", ContentFile(b"fake-webm-bytes"))
entry.audio_mime_type = "audio/webm"
entry.save(update_fields=["audio_mime_type"])
entry.refresh_from_db()
print("has_audio:", _history_dict(entry)["has_audio"], entry.audio_mime_type)
with entry.audio_file.open("rb") as f:
    print("audio bytes:", f.read())
storage, name = entry.audio_file.storage, entry.audio_file.name

for r in rows:
    print(_history_dict(r))
print(
    "other user sees:",
    FillyHistory.objects.exclude(user=user).filter(session_id="smoketest123").count(),
)
for r in rows:
    r.delete()  # model.delete removes the stored file
print("file removed from storage:", not storage.exists(name))
print("cleaned up")
