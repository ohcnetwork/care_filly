"""Sync ASR + LLM extraction pipeline (called from worker threads).

Provider-agnostic: the actual HTTP adapters live in
``care_filly.providers`` and are selected purely from plugin settings
(``ASR_PROVIDER`` / ``LLM_PROVIDER``) — nothing here knows about any
specific vendor.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from care_filly.providers import get_asr_provider, get_llm_provider
from care_filly.settings import mock_mode

logger = logging.getLogger("care_filly")

# ISO-639-1 codes commonly accepted as transcription language hints.
_ASR_LANGS = {
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


def resolve_language(language_hint: list[str]) -> str | None:
    for hint in language_hint or []:
        if hint == "auto_detect":
            return None
        code = hint.split("-")[0].lower()
        if code in _ASR_LANGS:
            return code
    return None


def transcribe_chunk(audio: bytes, filename: str, language: str | None) -> str:
    if mock_mode():
        return f"[mock transcript for {filename}]"

    text = get_asr_provider().transcribe(audio, filename, language)
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
    template_desc: str | None = None,
    template_example: str | None = None,
) -> tuple[dict[str, Any], dict | None]:
    """Run LLM extraction. Returns (structured_data, token_usage).

    token_usage is the provider's OpenAI-style usage object
    ({prompt_tokens, completion_tokens, total_tokens}) or None.
    """
    if mock_mode():
        return (
            {"clinical_notes": f"[mock extraction] {transcript[:200]}"},
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    system = template_desc or FALLBACK_INSTRUCTIONS
    if template_example:
        system += f"\n\nExample output format:\n{template_example}"
    system += (
        "\n\nRules: respond with a single valid JSON object and nothing else. "
        "Only include fields explicitly supported by the transcript. "
        "NEVER fabricate values."
    )

    content, usage = get_llm_provider().complete_json(
        system, f"Consultation transcript:\n\n{transcript}"
    )
    result = _parse_json(content)
    if not isinstance(result, dict):
        return {"clinical_notes": str(result)}, usage
    return result, usage
