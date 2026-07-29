"""Smoke test for user-scoped scribe history (run via manage.py shell)."""

from care_filly.api.viewsets.history import record_history
from care_filly.models import FillyHistory
from care_filly.resources.history import FillyHistoryReadSpec
from django.contrib.auth import get_user_model


def _history_dict(entry):
    return FillyHistoryReadSpec.serialize(entry).to_json()


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

# Attach + read back audio the way upload_history_audio does (object storage,
# e.g. Minio locally — requires a bucket to be configured, no local fallback).
entry = rows.filter(status="completed").first()
entry.save_audio(b"fake-webm-bytes", "audio/webm")
entry.save(update_fields=["internal_name", "audio_mime_type", "meta"])
entry.refresh_from_db()
print("has_audio:", _history_dict(entry)["has_audio"], entry.audio_mime_type)
print("signed read url:", entry.read_audio_url())
internal_name = entry.internal_name

for r in rows:
    print(_history_dict(r))
print(
    "other user sees:",
    FillyHistory.objects.exclude(user=user).filter(session_id="smoketest123").count(),
)
for r in rows:
    r.purge()  # hard delete + removes the stored recording from the bucket
print(
    "row purged:", not FillyHistory.objects.filter(internal_name=internal_name).exists()
)
print("cleaned up")
