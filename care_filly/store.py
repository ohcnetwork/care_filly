"""Cache-backed session store (shared across gunicorn workers via Django cache)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from django.core.cache import cache

from .plugin_settings import SESSION_TTL_SECONDS

_CHUNK_RE = re.compile(r"audio_(\d+)\.\w+$")
_LOCK_TTL = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skey(sid: str) -> str:
    return f"filly:session:{sid}"


def _ckey(sid: str, idx: int) -> str:
    return f"filly:chunk:{sid}:{idx}"


def _lkey(sid: str) -> str:
    return f"filly:lock:{sid}"


def parse_chunk_index(filename: str) -> Optional[int]:
    match = _CHUNK_RE.search(filename)
    return int(match.group(1)) if match else None


def create_session(session_id: str, **fields: Any) -> dict:
    created = datetime.now(timezone.utc)
    session = {
        "session_id": session_id,
        "status": "created",
        "created_at": created.isoformat(),
        "expires_at": (created + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat(),
        "completed_at": None,
        "templates": [],
        "language_hint": [],
        "additional_data": {},
        "patient_details": None,
        "audio_files": [],
        "chunk_indexes": [],
        "transcript": None,
        "template_results": {},
        "processing_errors": [],
        **fields,
    }
    cache.set(_skey(session_id), session, SESSION_TTL_SECONDS)
    return session


def get_session(session_id: str) -> Optional[dict]:
    return cache.get(_skey(session_id))


def save_session(session: dict) -> None:
    cache.set(_skey(session["session_id"]), session, SESSION_TTL_SECONDS)


def update_session(session_id: str, **updates: Any) -> Optional[dict]:
    """Read-modify-write under a short cache lock to avoid clobbering."""
    with session_lock(session_id):
        session = get_session(session_id)
        if session is None:
            return None
        session.update(updates)
        save_session(session)
        return session


class session_lock:
    def __init__(self, session_id: str) -> None:
        self.key = _lkey(session_id)

    def __enter__(self) -> None:
        deadline = time.monotonic() + _LOCK_TTL
        while not cache.add(self.key, 1, _LOCK_TTL):
            if time.monotonic() > deadline:
                break  # stale lock — proceed rather than deadlock
            time.sleep(0.02)

    def __exit__(self, *exc: Any) -> None:
        cache.delete(self.key)


def register_chunk(session_id: str, idx: int, filename: str) -> None:
    cache.set(
        _ckey(session_id, idx),
        {"file": filename, "text": None, "error": None},
        SESSION_TTL_SECONDS,
    )
    with session_lock(session_id):
        session = get_session(session_id)
        if session is None:
            return
        if idx not in session["chunk_indexes"]:
            session["chunk_indexes"].append(idx)
            session["audio_files"].append(filename)
        session["status"] = "recording"
        save_session(session)


def set_chunk_result(
    session_id: str, idx: int, text: Optional[str], error: Optional[str] = None
) -> None:
    chunk = cache.get(_ckey(session_id, idx)) or {"file": f"audio_{idx}.mp3"}
    chunk["text"] = text if text is not None else ""
    chunk["error"] = error
    cache.set(_ckey(session_id, idx), chunk, SESSION_TTL_SECONDS)


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
