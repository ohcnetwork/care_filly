"""Cache-backed hot path for a filly session's audio chunks.

The durable session state now lives in the ``FillySession`` DB row
(see ``care_filly.models.session``). Only the per-chunk audio bytes and
their transcript slots — the high-churn data touched while the doctor is
still speaking — live here in the Django cache, shared across gunicorn
workers and Celery workers.
"""

import re
import time

from django.core.cache import cache

from .settings import plugin_settings

_CHUNK_RE = re.compile(r"audio_(\d+)\.\w+$")
_LOCK_TTL = 5


def _ttl() -> int:
    return plugin_settings.SESSION_TTL_SECONDS


def _ckey(sid: str, idx: int) -> str:
    return f"filly:chunk:{sid}:{idx}"


def _akey(sid: str, idx: int) -> str:
    return f"filly:audio:{sid}:{idx}"


def _lkey(sid: str) -> str:
    return f"filly:lock:{sid}"


def parse_chunk_index(filename: str) -> int | None:
    match = _CHUNK_RE.search(filename)
    return int(match.group(1)) if match else None


class session_lock:
    """Short cache lock guarding read-modify-write of a session's chunk list."""

    def __init__(self, session_id: str) -> None:
        self.key = _lkey(session_id)

    def __enter__(self) -> None:
        deadline = time.monotonic() + _LOCK_TTL
        while not cache.add(self.key, 1, _LOCK_TTL):
            if time.monotonic() > deadline:
                break  # stale lock — proceed rather than deadlock
            time.sleep(0.02)

    def __exit__(self, *exc: object) -> None:
        cache.delete(self.key)


def store_chunk_audio(session_id: str, idx: int, audio: bytes) -> None:
    cache.set(_akey(session_id, idx), audio, _ttl())


def get_chunk_audio(session_id: str, idx: int) -> bytes | None:
    return cache.get(_akey(session_id, idx))


def register_chunk(session_id: str, idx: int, filename: str) -> None:
    cache.set(
        _ckey(session_id, idx),
        {"file": filename, "text": None, "error": None},
        _ttl(),
    )


def set_chunk_result(
    session_id: str, idx: int, text: str | None, error: str | None = None
) -> None:
    chunk = cache.get(_ckey(session_id, idx)) or {"file": f"audio_{idx}.mp3"}
    chunk["text"] = text if text is not None else ""
    chunk["error"] = error
    cache.set(_ckey(session_id, idx), chunk, _ttl())


def get_chunks(session_id: str, indexes: list[int]) -> dict[int, dict]:
    keys = {_ckey(session_id, i): i for i in indexes}
    found = cache.get_many(list(keys.keys()))
    return {keys[k]: v for k, v in found.items()}


def assemble_transcript(chunks: dict[int, dict]) -> str:
    parts = [
        (chunks[i].get("text") or "").strip()
        for i in sorted(chunks.keys())
        if chunks[i].get("text")
    ]
    return " ".join(p for p in parts if p).strip()


def all_chunks_done(chunks: dict[int, dict], expected: list[int]) -> bool:
    if len(chunks) < len(expected):
        return False
    return all(chunks[i].get("text") is not None for i in chunks)
