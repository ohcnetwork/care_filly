"""Mock providers for local dev + tests (no network, no API keys).

Activated either by selecting ``ASR_PROVIDER``/``LLM_PROVIDER`` = ``mock``
or by setting ``FILLY_MOCK=1`` (which forces both).
"""

from care_filly.providers.base import (
    ASRProvider,
    LLMProvider,
    register_asr,
    register_llm,
)


@register_asr("mock")
class MockASR(ASRProvider):
    def transcribe(self, audio: bytes, filename: str, language: str | None) -> str:
        return f"[mock transcript for {filename}]"


@register_llm("mock")
class MockLLM(LLMProvider):
    def extract(
        self,
        transcript: str,
        template_desc: str | None = None,
        template_example: str | None = None,
    ) -> tuple[dict, dict | None]:
        return (
            {"clinical_notes": f"[mock extraction] {transcript[:200]}"},
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
