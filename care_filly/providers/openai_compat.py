"""Adapters for any OpenAI-compatible API (OpenAI, Groq, Together, vLLM, ...)."""

from __future__ import annotations

import requests

from care_filly.providers import ASRProvider, LLMProvider


class OpenAICompatibleASR(ASRProvider):
    """`POST {base_url}/audio/transcriptions` (Whisper-style)."""

    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        api_key = self.config.require_api_key()
        data = {
            "model": self.config.model,
            "response_format": "json",
            "temperature": "0",
            **{k: str(v) for k, v in self.config.options.items()},
        }
        if language:
            data["language"] = language

        response = requests.post(
            f"{self.config.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (filename, audio, "audio/mpeg")},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("text", "")


class OpenAICompatibleLLM(LLMProvider):
    """`POST {base_url}/chat/completions` in JSON-object mode."""

    def complete_json(self, system: str, user: str) -> tuple[str, dict | None]:
        api_key = self.config.require_api_key()
        response = requests.post(
            f"{self.config.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                **self.config.options,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return content, payload.get("usage")
