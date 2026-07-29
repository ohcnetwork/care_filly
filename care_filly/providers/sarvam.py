"""Sarvam AI speech-to-text adapter (best for Indian languages)."""

from __future__ import annotations

import requests

from care_filly.providers import ASRProvider

# Languages Sarvam speech-to-text accepts as a BCP-47 hint (xx-IN)
_SARVAM_LANGS = {"hi", "bn", "kn", "ml", "mr", "od", "pa", "ta", "te", "en", "gu"}


class SarvamASR(ASRProvider):
    """`POST {base_url}/speech-to-text`.

    Options:
    - ``mode``: for saaras models — "translate" outputs English directly
      from Indic speech (best for form-fill; the extraction LLM works in
      English) while "transcribe" keeps the original language/script.
    """

    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        api_key = self.config.require_api_key()
        model = self.config.model
        data = {
            "model": model,
            "language_code": (
                f"{language}-IN" if language in _SARVAM_LANGS else "unknown"
            ),
        }
        if model.startswith("saaras"):
            data["mode"] = self.config.options.get("mode", "translate")

        response = requests.post(
            f"{self.config.base_url}/speech-to-text",
            headers={"api-subscription-key": api_key},
            data=data,
            files={"file": (filename, audio, "audio/mpeg")},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("transcript", "")
