"""Transcript -> structured form-fill JSON extraction via LLM (Groq or OpenAI)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from . import config

logger = logging.getLogger("filly.extraction")

_client = httpx.AsyncClient(timeout=60.0)

FALLBACK_INSTRUCTIONS = (
    "Extract clinically relevant information from the consultation transcript "
    'as a JSON object with a single key "clinical_notes" containing a concise '
    "clinical note. Output ONLY valid JSON."
)


def _build_messages(
    transcript: str,
    template_desc: Optional[str],
    template_example: Optional[str],
) -> list[dict[str, str]]:
    system = template_desc or FALLBACK_INSTRUCTIONS
    if template_example:
        system += f"\n\nExample output format:\n{template_example}"
    system += (
        "\n\nRules: respond with a single valid JSON object and nothing else. "
        "Only include fields explicitly supported by the transcript. "
        "NEVER fabricate values."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Consultation transcript:\n\n{transcript}"},
    ]


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


async def extract_structured(
    transcript: str,
    template_desc: Optional[str] = None,
    template_example: Optional[str] = None,
) -> dict[str, Any]:
    if config.MOCK_MODE:
        return {"clinical_notes": f"[mock extraction] {transcript[:200]}"}

    if config.LLM_PROVIDER == "openai":
        base_url, api_key, model = (
            config.OPENAI_BASE_URL,
            config.OPENAI_API_KEY,
            config.OPENAI_LLM_MODEL,
        )
    else:
        base_url, api_key, model = (
            config.GROQ_BASE_URL,
            config.GROQ_API_KEY,
            config.GROQ_LLM_MODEL,
        )

    if not api_key:
        raise RuntimeError(
            f"API key for LLM provider '{config.LLM_PROVIDER}' is not set"
        )

    response = await _client.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": _build_messages(transcript, template_desc, template_example),
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = _parse_json(content)
    logger.info("extraction produced %d top-level keys", len(result) if isinstance(result, dict) else -1)
    if not isinstance(result, dict):
        return {"clinical_notes": str(result)}
    return result
