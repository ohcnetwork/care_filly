"""Sync ASR + LLM extraction clients (called from worker threads)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import requests

from .plugin_settings import (
    GROQ_BASE_URL,
    OPENAI_BASE_URL,
    SARVAM_BASE_URL,
    get_setting,
    mock_mode,
)

logger = logging.getLogger("care_filly")

_WHISPER_LANGS = {
    "en",
    "hi",
    "gu",
    "kn",
    "ml",
    "ta",
    "te",
    "bn",
    "mr",
    "pa",
    "ur",
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "nl",
    "ru",
    "ja",
    "ko",
    "zh",
    "ar",
}

FALLBACK_INSTRUCTIONS = (
    "Extract clinically relevant information from the consultation transcript "
    'as a JSON object with a single key "clinical_notes" containing a concise '
    "clinical note. Output ONLY valid JSON."
    "\n- Always write all field values in ENGLISH."
    " If the consultation is in another language, translate the extracted values to English. Keep proper"
    " nouns, drug brand names, and medical codes as-is."
)


def resolve_language(language_hint: list[str]) -> Optional[str]:
    for hint in language_hint or []:
        if hint == "auto_detect":
            return None
        code = hint.split("-")[0].lower()
        if code in _WHISPER_LANGS:
            return code
    return None


# Languages Sarvam speech-to-text accepts as a BCP-47 hint (xx-IN)
_SARVAM_LANGS = {"hi", "bn", "kn", "ml", "mr", "od", "pa", "ta", "te", "en", "gu"}


def _transcribe_sarvam(audio: bytes, filename: str, language: Optional[str]) -> str:
    api_key = get_setting("SARVAM_API_KEY")
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured (or set FILLY_MOCK=1)")

    model = get_setting("SARVAM_ASR_MODEL")
    data = {
        "model": model,
        "language_code": (f"{language}-IN" if language in _SARVAM_LANGS else "unknown"),
    }
    if model.startswith("saaras"):
        data["mode"] = get_setting("SARVAM_ASR_MODE")

    response = requests.post(
        f"{SARVAM_BASE_URL}/speech-to-text",
        headers={"api-subscription-key": api_key},
        data=data,
        files={"file": (filename, audio, "audio/mpeg")},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("transcript", "")


def _transcribe_groq(audio: bytes, filename: str, language: Optional[str]) -> str:
    api_key = get_setting("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured (or set FILLY_MOCK=1)")

    data = {
        "model": get_setting("GROQ_ASR_MODEL"),
        "response_format": "json",
        "temperature": "0",
    }
    if language:
        data["language"] = language

    response = requests.post(
        f"{GROQ_BASE_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        data=data,
        files={"file": (filename, audio, "audio/mpeg")},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("text", "")


def transcribe_chunk(audio: bytes, filename: str, language: Optional[str]) -> str:
    if mock_mode():
        return f"[mock transcript for {filename}]"

    if get_setting("ASR_PROVIDER").lower() == "sarvam":
        text = _transcribe_sarvam(audio, filename, language)
    else:
        text = _transcribe_groq(audio, filename, language)
    logger.info(
        "transcribed %s (%d bytes) -> %d chars", filename, len(audio), len(text)
    )
    return text


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"clinical_notes": raw}


def extract_structured(
    transcript: str,
    template_desc: Optional[str] = None,
    template_example: Optional[str] = None,
) -> tuple[dict[str, Any], Optional[dict]]:
    """Run LLM extraction. Returns (structured_data, token_usage).

    token_usage is the provider's OpenAI-style usage object
    ({prompt_tokens, completion_tokens, total_tokens}) or None.
    """
    if mock_mode():
        return (
            {"clinical_notes": f"[mock extraction] {transcript[:200]}"},
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    if get_setting("LLM_PROVIDER").lower() == "openai":
        base_url = OPENAI_BASE_URL
        api_key = get_setting("OPENAI_API_KEY")
        model = get_setting("OPENAI_LLM_MODEL")
    else:
        base_url = GROQ_BASE_URL
        api_key = get_setting("GROQ_API_KEY")
        model = get_setting("GROQ_LLM_MODEL")

    if not api_key:
        raise RuntimeError("API key for the configured LLM provider is not set")

    system = template_desc or FALLBACK_INSTRUCTIONS
    if template_example:
        system += f"\n\nExample output format:\n{template_example}"
    system += (
        "\n\nRules: respond with a single valid JSON object and nothing else. "
        "Only include fields explicitly supported by the transcript. "
        "NEVER fabricate values."
    )

    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Consultation transcript:\n\n{transcript}",
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage")
    result = _parse_json(content)
    if not isinstance(result, dict):
        return {"clinical_notes": str(result)}, usage
    return result, usage
