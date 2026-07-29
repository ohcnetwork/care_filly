"""In-process smoke test for the refactored care_filly API (mock mode).

Run inside the backend container:
    FILLY_MOCK=1 python /tmp/filly_smoke.py
"""

import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

# Force Celery to execute tasks synchronously in-process so the smoke
# does not depend on a running worker.
from config.celery_app import app as celery_app  # noqa: E402

celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True  # surface exceptions

from care_filly.models import (  # noqa: E402
    FillyQuota,
    FillyUsage,
    FillyUserPreference,
)
from django.contrib.auth import get_user_model  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from care.facility.models.facility import Facility  # noqa: E402

User = get_user_model()
admin = User.objects.filter(is_superuser=True).first()
assert admin, "no superuser in DB"
facility = Facility.objects.first()
assert facility, "no facility in DB"
fid = str(facility.external_id)

# start clean
FillyQuota.objects.all().update(deleted=True)

c = APIClient()
c.force_authenticate(admin)
B = "/api/care_filly/v1"

# healthz + discovery (public)
pub = APIClient()
r = pub.get("/api/care_filly/healthz")
assert r.status_code == 200 and r.json()["mock"] is True, r.content
r = pub.get(f"{B}/.well-known/medscribealliance")
assert r.json()["protocol"] == "medscribealliance"
print("1. healthz + discovery OK")

# quota admin CRUD
r = c.post(
    f"{B}/quota",
    {"facility_external_id": fid, "tokens": 1000, "tokens_per_user": 100},
    format="json",
)
assert r.status_code == 200, r.content
q = r.json()
assert q["facility_external_id"] == fid and q["tokens"] == 1000, q
assert q["created_by"]["username"] == admin.username, q
qid = q["external_id"]

r = c.get(f"{B}/quota")
data = r.json()
assert data["count"] >= 1 and any(x["external_id"] == qid for x in data["results"]), (
    data
)

r = c.get(f"{B}/quota/{qid}")
assert r.json()["external_id"] == qid

r = c.patch(f"{B}/quota/{qid}", {"tokens": 2000}, format="json")
assert r.json()["tokens"] == 2000, r.content

# duplicate create must fail with the FE error shape
r = c.post(f"{B}/quota", {"facility_external_id": fid}, format="json")
assert r.status_code == 400 and r.json()["error"]["code"] == "quota_exists", r.content
print("2. quota admin CRUD OK")

# preference + accept-tnc + my
r = c.put(f"{B}/preferences/scribe", {"enabled": True}, format="json")
p = r.json()
assert p["enabled"] and p["tnc_accepted"], p

r = c.post(f"{B}/quota/accept-tnc", {"facility_id": fid}, format="json")
assert "accepted" in r.json()["detail"], r.content

r = c.get(f"{B}/quota/my", {"facility_id": fid})
my = r.json()
assert my["tnc_accepted"] is True and len(my["quotas"]) == 2, my
print("3. preference / accept-tnc / my OK")

# scribe session flow (mock providers)
r = c.post(
    f"{B}/sessions",
    {
        "templates": ["care_form"],
        "language_hint": ["auto_detect"],
        "facility_id": fid,
        "additional_data": {"care_template": {"desc": "extract", "example": "{}"}},
    },
    format="json",
)
assert r.status_code == 200, r.content
s = r.json()
sid = s["session_id"]
token = s["upload_url"]["uploadData"]["fields"]["token"]

for i in (1, 2):
    from io import BytesIO

    f = BytesIO(b"fake-mp3")
    f.name = f"audio_{i}.mp3"
    r = pub.post(
        f"{B}/upload/{sid}",
        {"token": token, "key": f"audio_{i}.mp3", "file": f},
        format="multipart",
    )
    assert r.status_code == 200 and r.json()["success"], r.content

r = c.post(f"{B}/sessions/{sid}/end", {}, format="json")
assert r.json()["audio_files_received"] == 2, r.content

deadline = time.time() + 30
status = None
while time.time() < deadline:
    status = c.get(f"{B}/sessions/{sid}").json()
    if status["status"] in ("completed", "partial", "failed"):
        break
    time.sleep(0.3)
assert status["status"] == "completed", status
assert status["transcript"] and "[mock transcript" in status["transcript"], status
assert status["templates"][0]["care_form"]["status"] == "success", status
assert status["usage"]["total_tokens"] == 150, status
print("4. session flow OK (mock transcript + extraction + usage)")

# usage row + quota "used"
assert FillyUsage.objects.filter(session_id=sid).exists()
r = c.get(f"{B}/quota/my", {"facility_id": fid})
assert any(x["used"] >= 150 for x in r.json()["quotas"]), r.json()

# history
r = c.get(f"{B}/history")
h = r.json()
assert h["count"] >= 1, h
entry = next(e for e in h["results"] if e["session_id"] == sid)
assert entry["status"] == "completed" and entry["has_audio"] is False, entry

r = c.delete(f"{B}/history/{entry['id']}")
assert r.status_code == 204, r.status_code
r = c.get(f"{B}/history")
assert all(e["id"] != entry["id"] for e in r.json()["results"])

r = c.delete(f"{B}/history")
assert r.status_code == 200, r.content
print("5. history list/delete/clear OK")

# quota destroy (facility row takes user rows along)
r = c.delete(f"{B}/quota/{qid}")
assert r.status_code == 204, r.status_code
assert not FillyQuota.objects.filter(facility_external_id=fid).exists()
print("6. quota destroy cascade OK")

# non-superuser is rejected from admin endpoints
plain = User.objects.filter(is_superuser=False).first()
if plain:
    c2 = APIClient()
    c2.force_authenticate(plain)
    r = c2.get(f"{B}/quota")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", r.content
    print("7. non-superuser forbidden OK")

# cleanup
FillyUserPreference.objects.filter(user=admin).delete()
print("\nALL CHECKS PASSED")
