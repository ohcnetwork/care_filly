"""Chunk transcription via Groq Whisper (OpenAI-compatible audio API)."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from . import config

logger = logging.getLogger("filly.asr")

_client = httpx.AsyncClient(timeout=30.0)

# Languages Whisper accepts as a forced-language hint (ISO 639-1)
_WHISPER_LANGS = {
    "en", "hi", "gu", "kn", "ml", "ta", "te", "bn", "mr", "pa", "ur",
    "es", "fr", "de", "it", "pt", "nl", "ru", "ja", "ko", "zh", "ar",
}


def resolve_language(language_hint: list[str]) -> Optional[str]:
    """Map the SDK language hint to a Whisper language code, or None for auto."""
    for hint in language_hint:
        if hint == "auto_detect":
            return None
        code = hint.split("-")[0].lower()
        if code in _WHISPER_LANGS:
            return code
    return None


# Languages Sarvam speech-to-text accepts as a BCP-47 hint (xx-IN)
_SARVAM_LANGS = {"hi", "bn", "kn", "ml", "mr", "od", "pa", "ta", "te", "en", "gu"}


async def _transcribe_sarvam(
    audio: bytes, filename: str, language: Optional[str]
) -> str:
    if not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not set (or set FILLY_MOCK=1)")

    data: dict[str, str] = {
        "model": config.SARVAM_ASR_MODEL,
        "language_code": (
            f"{language}-IN" if language in _SARVAM_LANGS else "unknown"
        ),
    }
    if config.SARVAM_ASR_MODEL.startswith("saaras"):
        data["mode"] = config.SARVAM_ASR_MODE

    response = await _client.post(
        f"{config.SARVAM_BASE_URL}/speech-to-text",
        headers={"api-subscription-key": config.SARVAM_API_KEY},
        data=data,
        files={"file": (filename, audio, "audio/mpeg")},
    )
    response.raise_for_status()
    return response.json().get("transcript", "")


async def _transcribe_groq(
    audio: bytes, filename: str, language: Optional[str]
) -> str:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set (or set FILLY_MOCK=1)")

    data: dict[str, str] = {
        "model": config.GROQ_ASR_MODEL,
        "response_format": "json",
        "temperature": "0",
    }
    if language:
        data["language"] = language

    response = await _client.post(
        f"{config.GROQ_BASE_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        data=data,
        files={"file": (filename, audio, "audio/mpeg")},
    )
    response.raise_for_status()
    return response.json().get("text", "")


async def transcribe_chunk(
    audio: bytes,
    filename: str,
    language: Optional[str] = None,
) -> str:
    if config.MOCK_MODE:
        return f"[mock transcript for {filename}]"

    if config.ASR_PROVIDER == "sarvam":
        text = await _transcribe_sarvam(audio, filename, language)
    else:
        text = await _transcribe_groq(audio, filename, language)
    logger.info("transcribed %s (%d bytes) -> %d chars", filename, len(audio), len(text))
    return text
