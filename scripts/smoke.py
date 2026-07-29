"""End-to-end smoke test against a running care_filly instance.

Every endpoint is CARE-JWT authenticated, so export a valid token first:

    export CARE_TOKEN="<jwt>"
    python scripts/smoke.py [base_url]
"""

import json
import os
import sys
import time
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090").rstrip("/")
TOKEN = os.environ.get("CARE_TOKEN", "")


def _auth_headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    if extra:
        headers.update(extra)
    return headers


def call(method: str, path: str, body=None, ctype="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    headers = _auth_headers({"Content-Type": ctype} if data else None)
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def upload_chunk(sid: str, index: int, audio: bytes):
    """POST a multipart chunk (file + index) the way the in-house recorder does."""
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}",
        f'Content-Disposition: form-data; name="file"; filename="{index}.wav"',
        "Content-Type: audio/wav",
        "",
    ]
    body = b"\r\n".join(p.encode() for p in parts) + b"\r\n" + audio + b"\r\n"
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="index"\r\n\r\n'
        f"{index}\r\n--{boundary}--\r\n"
    ).encode()
    headers = _auth_headers(
        {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    req = urllib.request.Request(
        f"{BASE}/v1/sessions/{sid}/chunks", data=body, method="POST", headers=headers
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


session = call(
    "POST",
    "/v1/sessions",
    body={
        "templates": ["care_form"],
        "language_hint": ["auto_detect"],
        "additional_data": {
            "care_template": {
                "desc": 'Extract JSON with key "Heart Rate"',
                "example": '{"Heart Rate": 90}',
            }
        },
    },
)
sid = session["session_id"]
for key in ("session_id", "status", "created_at", "expires_at"):
    assert session.get(key), f"missing {key}: {session}"
print("1. session created:", sid)

for i in (0, 1):
    r = upload_chunk(sid, i, b"fake-wav")
    assert r.get("success"), r
print("2. chunks uploaded (transcription kicked off during recording)")

end = call("POST", f"/v1/sessions/{sid}/end")
assert end["audio_files_received"] == 2, end
print("3. session ended:", end["status"])

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
print("4. completed — transcript:", repr(status["transcript"][:60]))
print("   template data:", json.dumps(templates[0]["care_form"]["data"])[:100])
print("\nALL CHECKS PASSED")
