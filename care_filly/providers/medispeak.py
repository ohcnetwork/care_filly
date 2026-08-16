"""Medispeak-hosted ASR provider.

Medispeak's v2 API is session-based (create session -> upload sequenced
audio chunks -> commit -> poll for the result), not a stateless "transcribe
this clip" endpoint like Sarvam/Whisper. To slot into the existing
per-chunk ``ASRProvider`` interface without reworking the chunk pipeline,
each VAD-segmented chunk is transcribed as its own single-chunk, transcript
-only Medispeak session.

Requests are made directly with the account secret (``MEDISPEAK_API_KEY``,
``msk_live_...``) since this all happens server-side — no browser-scoped
session token is needed here.
"""

import logging
import time

from care_filly.providers.base import ASRProvider, ProviderError, register_asr
from care_filly.providers.http import get, post
from care_filly.settings import plugin_settings

logger = logging.getLogger("care_filly")

_POLL_INTERVAL_SECONDS = 0.5
_POLL_TIMEOUT_SECONDS = 20
# The real job state lives in the response body's "status" field — the
# HTTP status code is always 200, regardless of created/processing/etc.
_TERMINAL_STATUSES = {"completed", "partial", "failed"}


@register_asr("medispeak")
class MedispeakASR(ASRProvider):
    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        api_key = plugin_settings.MEDISPEAK_API_KEY
        if not api_key:
            raise ProviderError(
                "MEDISPEAK_API_KEY is not configured (or set FILLY_MOCK=1)"
            )
        base_url = plugin_settings.MEDISPEAK_BASE_URL
        if not base_url:
            raise ProviderError("MEDISPEAK_BASE_URL is not configured")
        base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}

        session = post(
            f"{base_url}/scribe_sessions",
            headers=headers,
            json={
                "outputs": [{"type": "transcript"}],
                "language": [language] if language else ["auto"],
                "mode": "consultation",
            },
            timeout=10,
        ).json()
        session_id = session["id"]

        post(
            f"{base_url}/scribe_sessions/{session_id}/audio/chunks",
            headers=headers,
            data={"seq": "0", "final": "true"},
            files={"chunk": (filename, audio, "audio/wav")},
            timeout=30,
        )
        post(
            f"{base_url}/scribe_sessions/{session_id}/commit",
            headers=headers,
            timeout=10,
        )

        text = self._poll_transcript(base_url, session_id, headers)
        logger.info(
            "medispeak transcribed %s (%d bytes) -> %d chars",
            filename,
            len(audio),
            len(text),
        )
        return text

    @staticmethod
    def _poll_transcript(base_url: str, session_id: str, headers: dict) -> str:
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        payload: dict = {}
        while True:
            payload = get(
                f"{base_url}/scribe_sessions/{session_id}",
                headers=headers,
                timeout=10,
            ).json()
            if payload.get("status") in _TERMINAL_STATUSES:
                break
            if time.monotonic() > deadline:
                raise ProviderError("Medispeak session timed out waiting for result")
            time.sleep(_POLL_INTERVAL_SECONDS)

        if payload.get("status") == "failed":
            errors = [
                err
                for output in payload.get("outputs", [])
                for err in output.get("errors", [])
            ]
            logger.warning(
                "medispeak session %s failed: errors=%s", session_id, errors
            )
            return ""

        if payload.get("transcript"):
            return payload["transcript"]

        for output in payload.get("outputs", []):
            text = (output.get("result") or {}).get("text")
            if text:
                return text

        # Nothing matched the expected shape — log the raw payload so the
        # actual output structure can be inspected instead of guessing.
        logger.warning(
            "medispeak session %s completed with no transcript text; raw=%s",
            session_id,
            payload,
        )
        return ""
