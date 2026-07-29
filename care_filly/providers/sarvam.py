"""Sarvam AI speech-to-text provider (best for Indian languages)."""

import logging

from care_filly.providers.base import ASRProvider, ProviderError, register_asr
from care_filly.providers.http import post
from care_filly.providers.languages import SARVAM_LANGS
from care_filly.settings import plugin_settings

logger = logging.getLogger("care_filly")


@register_asr("sarvam")
class SarvamASR(ASRProvider):
    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        api_key = plugin_settings.ASR_API_KEY
        if not api_key:
            raise ProviderError("ASR_API_KEY is not configured (or set FILLY_MOCK=1)")

        model = plugin_settings.ASR_MODEL
        data = {
            "model": model,
            "language_code": (
                f"{language}-IN" if language in SARVAM_LANGS else "unknown"
            ),
        }
        # saaras models translate/transcribe Indic speech; whisper-style
        # models don't take a mode.
        if model.startswith("saaras"):
            data["mode"] = plugin_settings.SARVAM_ASR_MODE

        base_url = plugin_settings.ASR_BASE_URL.rstrip("/")
        response = post(
            f"{base_url}/speech-to-text",
            headers={"api-subscription-key": api_key},
            data=data,
            files={"file": (filename, audio, "audio/mpeg")},
            timeout=30,
        )
        text = response.json().get("transcript", "")
        logger.info(
            "transcribed %s (%d bytes) -> %d chars", filename, len(audio), len(text)
        )
        return text
