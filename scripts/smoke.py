"""End-to-end smoke test against a running care_filly instance.

Usage: python scripts/smoke.py [base_url]
"""

import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090").rstrip("/")


def call(method: str, path: str, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method, headers={"Content-Type": ctype}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


disc = call("GET", "/v1/.well-known/medscribealliance")
assert disc["protocol"] == "medscribealliance", disc
print("1. discovery OK:", disc["endpoints"]["base_url"])

session = call(
    "POST",
    "/v1/sessions",
    body={
        "templates": ["care_form"],
        "upload_type": "chunked",
        "communication_protocol": "rest",
        "model": "pro",
        "language_hint": ["auto_detect"],
        "session_mode": "consultation",
        "additional_data": {
            "care_template": {
                "desc": 'Extract JSON with key "Heart Rate"',
                "example": '{"Heart Rate": 90}',
            }
        },
    },
)
sid = session["session_id"]
for key in ("session_id", "status", "created_at", "expires_at", "upload_url"):
    assert session.get(key), f"missing {key}: {session}"
print("2. session created:", sid)

for i in (1, 2):
    r = call("POST", f"/v1/upload/{sid}/audio_{i}.mp3", raw=b"fake-mp3", ctype="audio/mp3")
    assert r.get("success"), r
print("3. chunks uploaded (transcription kicked off during recording)")

end = call("POST", f"/v1/sessions/{sid}/end", body={"audio_files_sent": 2, "audio_files_uploaded": 2})
assert end["audio_files_received"] == 2, end
print("4. session ended:", end["status"])

deadline = time.time() + 60
while time.time() < deadline:
    status = call("GET", f"/v1/sessions/{sid}")
    if status["status"] in ("completed", "partial", "failed"):
        break
    time.sleep(0.5)

assert status["status"] == "completed", status
assert status["transcript"], status
templates = status["templates"]
assert templates and templates[0]["care_form"]["status"] == "success", templates
print("5. completed — transcript:", repr(status["transcript"][:60]))
print("   template data:", json.dumps(templates[0]["care_form"]["data"])[:100])
print("\nALL CHECKS PASSED")
