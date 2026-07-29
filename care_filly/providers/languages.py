"""Language-hint resolution shared across ASR providers."""

# Whisper-family language codes (openai-compatible ASR).
WHISPER_LANGS = {
    "en", "hi", "gu", "kn", "ml", "ta", "te", "bn", "mr", "pa", "ur",
    "es", "fr", "de", "it", "pt", "nl", "ru", "ja", "ko", "zh", "ar",
}  # fmt: skip

# Languages Sarvam speech-to-text accepts as a BCP-47 hint (xx-IN).
SARVAM_LANGS = {"hi", "bn", "kn", "ml", "mr", "od", "pa", "ta", "te", "en", "gu"}


def resolve_language(language_hint: list[str]) -> str | None:
    """Pick the first usable ISO-639-1 code from a list of hints.

    ``"auto_detect"`` (and anything unrecognised) yields ``None`` so the
    provider auto-detects.
    """
    for hint in language_hint or []:
        if hint == "auto_detect":
            return None
        code = hint.split("-")[0].lower()
        if code in WHISPER_LANGS:
            return code
    return None
