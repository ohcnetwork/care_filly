"""Pluggable AI provider adapters for care_filly.

No provider names are hardcoded anywhere else in the plugin. The active
ASR (speech-to-text) and LLM (structured extraction) providers are picked
entirely from plugin settings:

    ASR_PROVIDER  = "sarvam" | "openai" | "dotted.path.ToClass"
    ASR_BASE_URL  = provider API origin
    ASR_MODEL     = model identifier
    ASR_API_KEY   = credential
    ASR_OPTIONS   = provider-specific extras (dict)

    LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_OPTIONS

Built-in adapters:
- "openai": any OpenAI-compatible API (OpenAI, Groq, Together, vLLM,
  LiteLLM proxies, ...) for both ASR (`/audio/transcriptions`) and LLM
  (`/chat/completions`).
- "sarvam": Sarvam AI speech-to-text (best for Indian languages).

Deployments can point ``*_PROVIDER`` at a dotted class path to plug in a
custom adapter without touching this package.
"""

from __future__ import annotations

from typing import Optional

from django.utils.module_loading import import_string


class ProviderConfig:
    """Resolved connection settings handed to every adapter."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        options: dict | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.options = options or {}

    def require_api_key(self) -> str:
        if not self.api_key:
            msg = "API key for the configured provider is not set (or set FILLY_MOCK=1)"
            raise RuntimeError(msg)
        return self.api_key


class ASRProvider:
    """Speech-to-text adapter interface."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        """Transcribe one audio chunk, returning plain text."""
        raise NotImplementedError


class LLMProvider:
    """JSON-mode chat-completion adapter interface."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def complete_json(self, system: str, user: str) -> tuple[str, dict | None]:
        """Run a JSON-object completion.

        Returns (raw_content, usage) where usage is the provider's
        OpenAI-style usage object ({prompt_tokens, completion_tokens,
        total_tokens}) or None.
        """
        raise NotImplementedError


ASR_PROVIDERS = {
    "openai": "care_filly.providers.openai_compat.OpenAICompatibleASR",
    "sarvam": "care_filly.providers.sarvam.SarvamASR",
}

LLM_PROVIDERS = {
    "openai": "care_filly.providers.openai_compat.OpenAICompatibleLLM",
}


def _resolve(name: str, registry: dict[str, str], kind: str) -> type:
    path = registry.get(name.lower(), name)
    try:
        return import_string(path)
    except ImportError as exc:
        msg = (
            f"Unknown {kind} provider {name!r} — use one of "
            f"{sorted(registry)} or a dotted class path"
        )
        raise RuntimeError(msg) from exc


def get_asr_provider() -> ASRProvider:
    from care_filly.settings import plugin_settings

    cls = _resolve(plugin_settings.ASR_PROVIDER, ASR_PROVIDERS, "ASR")
    return cls(
        ProviderConfig(
            base_url=plugin_settings.ASR_BASE_URL,
            api_key=plugin_settings.ASR_API_KEY,
            model=plugin_settings.ASR_MODEL,
            options=dict(plugin_settings.ASR_OPTIONS or {}),
        )
    )


def get_llm_provider() -> LLMProvider:
    from care_filly.settings import plugin_settings

    cls = _resolve(plugin_settings.LLM_PROVIDER, LLM_PROVIDERS, "LLM")
    return cls(
        ProviderConfig(
            base_url=plugin_settings.LLM_BASE_URL,
            api_key=plugin_settings.LLM_API_KEY,
            model=plugin_settings.LLM_MODEL,
            options=dict(plugin_settings.LLM_OPTIONS or {}),
        )
    )
