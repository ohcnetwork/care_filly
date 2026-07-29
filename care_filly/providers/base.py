"""Vendor-agnostic ASR + LLM provider registry.

Providers are plain, dependency-light classes registered under a string
key. The active provider is selected at call time from plugin settings
(``ASR_PROVIDER`` / ``LLM_PROVIDER``), so swapping vendors is a config
change, not a code change. ``FILLY_MOCK=1`` forces the ``mock`` provider
for both, keeping local dev and tests free of network + API keys.
"""

import abc
import logging
from collections.abc import Callable

logger = logging.getLogger("care_filly")


class ProviderError(Exception):
    """Permanent provider failure — do not retry."""


class TransientProviderError(ProviderError):
    """Temporary provider failure (timeout, 5xx, rate limit) — safe to retry."""


class ASRProvider(abc.ABC):
    """Speech-to-text provider."""

    @abc.abstractmethod
    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        """Return the transcript for a single audio chunk."""


class LLMProvider(abc.ABC):
    """Structured-extraction provider."""

    @abc.abstractmethod
    def extract(
        self,
        transcript: str,
        template_desc: str | None = None,
        template_example: str | None = None,
    ) -> tuple[dict, dict | None]:
        """Return ``(structured_data, token_usage)``.

        ``token_usage`` is the OpenAI-style usage object
        (``{prompt_tokens, completion_tokens, total_tokens}``) or ``None``.
        """


_ASR_REGISTRY: dict[str, Callable[[], ASRProvider]] = {}
_LLM_REGISTRY: dict[str, Callable[[], LLMProvider]] = {}


def register_asr(name: str) -> Callable[[type], type]:
    def wrapper(cls: type) -> type:
        _ASR_REGISTRY[name.lower()] = cls
        return cls

    return wrapper


def register_llm(name: str) -> Callable[[type], type]:
    def wrapper(cls: type) -> type:
        _LLM_REGISTRY[name.lower()] = cls
        return cls

    return wrapper


def _mock_mode() -> bool:
    from care_filly.settings import mock_mode

    return mock_mode()


def get_asr_provider() -> ASRProvider:
    from care_filly.settings import plugin_settings

    # Import for registration side effects.
    from care_filly.providers import mock, openai_compat, sarvam  # noqa: F401

    name = "mock" if _mock_mode() else str(plugin_settings.ASR_PROVIDER).lower()
    factory = _ASR_REGISTRY.get(name)
    if factory is None:
        raise ProviderError(f"Unknown ASR provider: {name!r}")
    return factory()


def get_llm_provider() -> LLMProvider:
    from care_filly.settings import plugin_settings

    from care_filly.providers import mock, openai_compat, sarvam  # noqa: F401

    name = "mock" if _mock_mode() else str(plugin_settings.LLM_PROVIDER).lower()
    factory = _LLM_REGISTRY.get(name)
    if factory is None:
        raise ProviderError(f"Unknown LLM provider: {name!r}")
    return factory()
