"""OpenAI-compatible ASR + LLM providers.

A single implementation covers OpenAI, Groq and any vendor exposing the
same ``/audio/transcriptions`` and ``/chat/completions`` endpoints — the
base URL, API key and model are all configurable, so there is no
per-vendor branching.
"""

import json
import logging
import re
from typing import Any

from care_filly.providers.base import (
    ASRProvider,
    LLMProvider,
    ProviderError,
    register_asr,
    register_llm,
)
from care_filly.providers.http import post
from care_filly.settings import plugin_settings

logger = logging.getLogger("care_filly")

FALLBACK_INSTRUCTIONS = (
    "Extract clinically relevant information from the consultation transcript "
    'as a JSON object with a single key "clinical_notes" containing a concise '
    "clinical note. Output ONLY valid JSON."
    "\n- Always write all field values in ENGLISH."
    " If the consultation is in another language, translate the extracted values"
    " to English. Keep proper nouns, drug brand names, and medical codes as-is."
)


@register_asr("openai_compat")
class OpenAICompatASR(ASRProvider):
    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        api_key = plugin_settings.ASR_API_KEY
        if not api_key:
            raise ProviderError("ASR_API_KEY is not configured (or set FILLY_MOCK=1)")

        data = {
            "model": plugin_settings.ASR_MODEL,
            "response_format": "json",
            "temperature": "0",
        }
        if language:
            data["language"] = language

        base_url = plugin_settings.ASR_BASE_URL.rstrip("/")
        response = post(
            f"{base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (filename, audio, "audio/mpeg")},
            timeout=30,
        )
        text = response.json().get("text", "")
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


@register_llm("openai_compat")
class OpenAICompatLLM(LLMProvider):
    def extract(
        self,
        transcript: str,
        template_desc: str | None = None,
        template_example: str | None = None,
    ) -> tuple[dict, dict | None]:
        api_key = plugin_settings.LLM_API_KEY
        if not api_key:
            raise ProviderError("LLM_API_KEY is not configured (or set FILLY_MOCK=1)")

        system = template_desc or FALLBACK_INSTRUCTIONS
        if template_example:
            system += f"\n\nExample output format:\n{template_example}"
        system += (
            "\n\nRules: respond with a single valid JSON object and nothing else. "
            "Only include fields explicitly supported by the transcript. "
            "NEVER fabricate values."
        )

        base_url = plugin_settings.LLM_BASE_URL.rstrip("/")
        response = post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": plugin_settings.LLM_MODEL,
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
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage")
        result = _parse_json(content)
        if not isinstance(result, dict):
            return {"clinical_notes": str(result)}, usage
        return result, usage
