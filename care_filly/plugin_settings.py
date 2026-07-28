"""Plugin settings: read from CARE's PLUGIN_CONFIGS, falling back to env vars."""

import os

from django.conf import settings

PLUGIN_NAME = "care_filly"

DEFAULTS = {
    "GROQ_API_KEY": "",
    "LLM_PROVIDER": "groq",
    "OPENAI_API_KEY": "",
    # ASR provider: "sarvam" (Sarvam AI — best for Indian languages like Tamil)
    # or "groq" (Whisper).
    "ASR_PROVIDER": "sarvam",
    "SARVAM_API_KEY": "",
    "SARVAM_ASR_MODEL": "saaras:v3",
    # saaras:v3 mode: "translate" outputs English directly from Indic speech
    # (best for form-fill — the extraction LLM works in English);
    # "transcribe" keeps the original language/script.
    "SARVAM_ASR_MODE": "translate",
    # Full large-v3 (not turbo): turbo's pruned decoder is noticeably worse
    # for low-resource languages like Tamil.
    "GROQ_ASR_MODEL": "whisper-large-v3-turbo",
    "GROQ_LLM_MODEL": "llama-3.3-70b-versatile",
    "OPENAI_LLM_MODEL": "gpt-4o-mini",
    "FILLY_AUTH_TOKEN": "",
    "FILLY_MOCK": "0",
    # Terms & conditions shown to users before their first scribe session.
    # Changing the text invalidates previous acceptances (hash comparison).
    "FILLY_TNC": "Welcome to Care Filly. By accessing or using Care Filly, you agree to these Terms and Conditions. Care Filly is an AI-assisted medical scribe module within CARE, an open-source digital healthcare platform, that assists healthcare professionals and organizations with medical documentation, AI-assisted transcription, structured clinical form-fill, and related documentation workflows. You must be at least 18 years old and legally capable of entering into binding agreements to use the Service. Healthcare professionals are solely responsible for all medical decisions, diagnoses, prescriptions, treatments, and compliance with applicable laws. AI-generated documentation is provided for assistance only and must always be reviewed and verified before clinical use. Care Filly does not provide medical advice or emergency healthcare services. Users are responsible for maintaining the confidentiality of their account credentials and for all activities conducted under their account. Users retain ownership of their uploaded content but grant Care Filly a limited license to store, process, transmit, and display such content solely for providing the Service. Users represent that they have obtained all legally required patient consents before recording, transcribing, or storing patient information. Users shall not upload unlawful content, attempt unauthorized access, reverse engineer the software, introduce malicious code, misuse patient information, or violate applicable laws. All software, trademarks, logos, source code, interfaces, AI models, and intellectual property remain the exclusive property of their respective owners within the CARE ecosystem. The Service may integrate with third-party services including AI transcription and language-model providers, cloud infrastructure providers, and other CARE modules. Care Filly is not responsible for third-party services or interruptions beyond its control. While Care Filly implements commercially reasonable security measures including encryption, authentication, and secure infrastructure, no online service can guarantee absolute security. Users acknowledge the inherent risks associated with internet-based systems. Care Filly does not warrant uninterrupted availability and may perform maintenance, upgrades, security patches, or feature changes without prior notice. To the maximum extent permitted by law, Care Filly shall not be liable for indirect, incidental, consequential, special, or punitive damages, including loss of profits, loss of business, data loss, or clinical decisions made using the Service. Users agree to indemnify and hold harmless Care Filly, CARE, their employees, officers, affiliates, licensors, and partners against claims arising from misuse of the Service, violation of these Terms, infringement of third-party rights, or unlawful activities. Care Filly may suspend or terminate access that violates these Terms or applicable laws. Certain information may be retained after termination where legally required. These Terms are governed by the laws of the Republic of India. Any disputes shall first be resolved through good-faith negotiations and, if unresolved, by arbitration under the Arbitration and Conciliation Act, 1996, with the seat of arbitration in Chennai, Tamil Nadu, India. Courts in Chennai shall have exclusive jurisdiction where applicable. If any provision of these Terms is held invalid or unenforceable, the remaining provisions shall remain in full force and effect. Care Filly may update these Terms at any time, and continued use of the Service constitutes acceptance of the revised Terms. By using Care Filly, you acknowledge that you have read, understood, and agreed to these Terms and Conditions.",
}

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
SARVAM_BASE_URL = "https://api.sarvam.ai"
SESSION_TTL_SECONDS = 3600


def get_setting(name: str) -> str:
    configs = getattr(settings, "PLUGIN_CONFIGS", {}).get(PLUGIN_NAME, {})
    value = configs.get(name) or os.getenv(name) or DEFAULTS.get(name, "")
    return str(value).strip()


def mock_mode() -> bool:
    return get_setting("FILLY_MOCK") == "1"
