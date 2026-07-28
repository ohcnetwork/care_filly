import os

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


GROQ_API_KEY = _env("GROQ_API_KEY")
LLM_PROVIDER = _env("LLM_PROVIDER", "groq").lower()
OPENAI_API_KEY = _env("OPENAI_API_KEY")

# ASR provider: "sarvam" (Sarvam AI — best for Indian languages like Tamil)
# or "groq" (Whisper).
ASR_PROVIDER = _env("ASR_PROVIDER", "sarvam").lower()
SARVAM_API_KEY = _env("SARVAM_API_KEY")
SARVAM_ASR_MODEL = _env("SARVAM_ASR_MODEL", "saaras:v3")
# saaras:v3 mode: "translate" outputs English directly from Indic speech
# (best for form-fill); "transcribe" keeps the original language/script.
SARVAM_ASR_MODE = _env("SARVAM_ASR_MODE", "translate")

# Full large-v3 (not turbo): turbo is noticeably worse for Tamil & other
# low-resource languages.
GROQ_ASR_MODEL = _env("GROQ_ASR_MODEL", "whisper-large-v3-turbo")
GROQ_LLM_MODEL = _env("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
OPENAI_LLM_MODEL = _env("OPENAI_LLM_MODEL", "gpt-4o-mini")

FILLY_AUTH_TOKEN = _env("FILLY_AUTH_TOKEN")
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://localhost:8090").rstrip("/")
MOCK_MODE = _env("FILLY_MOCK", "0") == "1"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
SARVAM_BASE_URL = "https://api.sarvam.ai"

SESSION_TTL_SECONDS = 3600
