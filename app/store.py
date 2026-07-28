"""In-memory session store for scribe sessions."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config

_CHUNK_RE = re.compile(r"audio_(\d+)\.\w+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    session_id: str
    created_at: str
    expires_at: str
    templates: list[str] = field(default_factory=list)
    language_hint: list[str] = field(default_factory=list)
    additional_data: dict[str, Any] = field(default_factory=dict)
    patient_details: Optional[dict[str, Any]] = None

    # created | recording | processing | completed | partial | failed
    status: str = "created"
    audio_files: list[str] = field(default_factory=list)
    # chunk index -> transcript text (None while in flight)
    chunk_transcripts: dict[int, Optional[str]] = field(default_factory=dict)
    chunk_tasks: list[asyncio.Task] = field(default_factory=list)
    transcript: Optional[str] = None
    language_detected: Optional[str] = None
    # template_id -> {"status": ..., "data": ...}
    template_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    processing_errors: list[dict[str, str]] = field(default_factory=list)
    completed_at: Optional[str] = None
    finalize_task: Optional[asyncio.Task] = None
    _created_monotonic: float = field(default_factory=time.monotonic)

    def assemble_transcript(self) -> str:
        parts = [
            text
            for _idx, text in sorted(self.chunk_transcripts.items())
            if text
        ]
        return " ".join(p.strip() for p in parts if p.strip()).strip()

    def all_chunks_transcribed(self) -> bool:
        return all(t is not None for t in self.chunk_transcripts.values())


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str, **kwargs: Any) -> Session:
        created = datetime.now(timezone.utc)
        session = Session(
            session_id=session_id,
            created_at=created.isoformat(),
            expires_at=(
                created + timedelta(seconds=config.SESSION_TTL_SECONDS)
            ).isoformat(),
            **kwargs,
        )
        self._sessions[session_id] = session
        self._evict_expired()
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - config.SESSION_TTL_SECONDS * 2
        stale = [
            sid
            for sid, s in self._sessions.items()
            if s._created_monotonic < cutoff
        ]
        for sid in stale:
            del self._sessions[sid]


def parse_chunk_index(filename: str) -> Optional[int]:
    match = _CHUNK_RE.search(filename)
    return int(match.group(1)) if match else None


store = SessionStore()
