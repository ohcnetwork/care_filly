from typing import Any

import environ
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from rest_framework.settings import perform_import

from care_filly.apps import PLUGIN_NAME

env = environ.Env()


class PluginSettings:  # pragma: no cover
    """
    A settings object that allows plugin settings to be accessed as
    properties. For example:

        from care_filly.settings import plugin_settings
        print(plugin_settings.ASR_API_KEY)

    Any setting with string import paths will be automatically resolved
    and return the class, rather than the string literal.
    """

    def __init__(
        self,
        plugin_name: str = None,
        defaults: dict | None = None,
        import_strings: set | None = None,
        required_settings: set | None = None,
    ) -> None:
        if not plugin_name:
            raise ValueError("Plugin name must be provided")
        self.plugin_name = plugin_name
        self.defaults = defaults or {}
        self.import_strings = import_strings or set()
        self.required_settings = required_settings or set()
        self._cached_attrs = set()
        self.validate()

    def __getattr__(self, attr) -> Any:
        if attr not in self.defaults:
            raise AttributeError("Invalid setting: '%s'" % attr)

        # Try to find the setting from user settings, then from
        # environment variables
        val = self.defaults[attr]
        try:
            val = self.user_settings[attr]
        except KeyError:
            try:
                val = env(attr, cast=type(val))
            except environ.ImproperlyConfigured:
                # Fall back to defaults
                pass

        # Coerce import strings into classes
        if attr in self.import_strings:
            val = perform_import(val, attr)

        self._cached_attrs.add(attr)
        setattr(self, attr, val)
        return val

    @property
    def user_settings(self) -> dict:
        if not hasattr(self, "_user_settings"):
            self._user_settings = getattr(settings, "PLUGIN_CONFIGS", {}).get(
                self.plugin_name, {}
            )
        return self._user_settings

    def validate(self) -> None:
        """
        This method handles the validation of the plugin settings.
        It could be overridden to provide custom validation logic.

        the base implementation checks if all the required settings are
        truthy.
        """
        for setting in self.required_settings:
            if not getattr(self, setting):
                raise ImproperlyConfigured(
                    f'The "{setting}" setting is required. '
                    f'Please set the "{setting}" in the environment or the '
                    f"{PLUGIN_NAME} plugin config."
                )

    def reload(self) -> None:
        """
        Deletes the cached attributes so they will be recomputed next
        time they are accessed.
        """
        for attr in self._cached_attrs:
            delattr(self, attr)
        self._cached_attrs.clear()
        if hasattr(self, "_user_settings"):
            delattr(self, "_user_settings")


REQUIRED_SETTINGS: set = set()

DEFAULTS = {
    # --- Provider selection -------------------------------------------------
    # ASR: "sarvam" (best for Indian languages), "openai_compat" (Whisper via
    # any OpenAI-compatible vendor) or "mock". LLM: "openai_compat" or "mock".
    "ASR_PROVIDER": "sarvam",
    "LLM_PROVIDER": "openai_compat",
    # FILLY_MOCK=1 forces both providers to "mock" (no network / API keys).
    "FILLY_MOCK": "0",
    # --- ASR (speech-to-text): one key/base_url/model per capability, used
    # by whichever ASR_PROVIDER is active. Defaults target Sarvam (the default
    # provider). For openai_compat/Whisper set ASR_BASE_URL to the vendor's
    # OpenAI-compatible base (e.g. https://api.openai.com/v1) and
    # ASR_MODEL to e.g. whisper-1.
    "ASR_BASE_URL": "https://api.sarvam.ai",
    "ASR_API_KEY": "",
    "ASR_MODEL": "saaras:v3",
    # saaras mode: "translate" outputs English directly from Indic speech
    # (best for form-fill); "transcribe" keeps the original language/script.
    "SARVAM_ASR_MODE": "translate",
    # --- LLM (structured extraction): OpenAI-compatible ---------------------
    "LLM_BASE_URL": "https://api.openai.com/v1",
    "LLM_API_KEY": "",
    "LLM_MODEL": "gpt-4o-mini",
    # --- Session / pipeline tuning -----------------------------------------
    # Seconds of audio per uploaded chunk (used to estimate recorded duration).
    "CHUNK_SECONDS": 20,
    "SESSION_TTL_SECONDS": 3600,
    "FINALIZE_TIMEOUT_SECONDS": 90,
    "MAX_AUDIO_UPLOAD_MB": 100,
    # --- Terms & conditions ------------------------------------------------
    # Active Terms & Conditions text. Override via the FILLY_TNC env var or
    # plugin config. Changing the text invalidates previous acceptances
    # (hash comparison).
    "FILLY_TNC": "Welcome to Care Filly. By accessing or using Care Filly, you agree to these Terms and Conditions. Care Filly is an AI-assisted medical scribe module within CARE, an open-source digital healthcare platform, that assists healthcare professionals and organizations with medical documentation, AI-assisted transcription, structured clinical form-fill, and related documentation workflows. You must be at least 18 years old and legally capable of entering into binding agreements to use the Service. Healthcare professionals are solely responsible for all medical decisions, diagnoses, prescriptions, treatments, and compliance with applicable laws. AI-generated documentation is provided for assistance only and must always be reviewed and verified before clinical use. Care Filly does not provide medical advice or emergency healthcare services. Users are responsible for maintaining the confidentiality of their account credentials and for all activities conducted under their account. Users retain ownership of their uploaded content but grant Care Filly a limited license to store, process, transmit, and display such content solely for providing the Service. Users represent that they have obtained all legally required patient consents before recording, transcribing, or storing patient information. Users shall not upload unlawful content, attempt unauthorized access, reverse engineer the software, introduce malicious code, misuse patient information, or violate applicable laws. All software, trademarks, logos, source code, interfaces, AI models, and intellectual property remain the exclusive property of their respective owners within the CARE ecosystem. The Service may integrate with third-party services including AI transcription and language-model providers, cloud infrastructure providers, and other CARE modules. Care Filly is not responsible for third-party services or interruptions beyond its control. While Care Filly implements commercially reasonable security measures including encryption, authentication, and secure infrastructure, no online service can guarantee absolute security. Users acknowledge the inherent risks associated with internet-based systems. Care Filly does not warrant uninterrupted availability and may perform maintenance, upgrades, security patches, or feature changes without prior notice. To the maximum extent permitted by law, Care Filly shall not be liable for indirect, incidental, consequential, special, or punitive damages, including loss of profits, loss of business, data loss, or clinical decisions made using the Service. Users agree to indemnify and hold harmless Care Filly, CARE, their employees, officers, affiliates, licensors, and partners against claims arising from misuse of the Service, violation of these Terms, infringement of third-party rights, or unlawful activities. Care Filly may suspend or terminate access that violates these Terms or applicable laws. Certain information may be retained after termination where legally required. These Terms are governed by the laws of the Republic of India. Any disputes shall first be resolved through good-faith negotiations and, if unresolved, by arbitration under the Arbitration and Conciliation Act, 1996, with the seat of arbitration in Chennai, Tamil Nadu, India. Courts in Chennai shall have exclusive jurisdiction where applicable. If any provision of these Terms is held invalid or unenforceable, the remaining provisions shall remain in full force and effect. Care Filly may update these Terms at any time, and continued use of the Service constitutes acceptance of the revised Terms. By using Care Filly, you acknowledge that you have read, understood, and agreed to these Terms and Conditions.",
}

plugin_settings = PluginSettings(
    PLUGIN_NAME, defaults=DEFAULTS, required_settings=REQUIRED_SETTINGS
)


def terms_and_conditions() -> str:
    """The active TnC text (from the FILLY_TNC setting / env override)."""
    return (plugin_settings.FILLY_TNC or "").strip()


def mock_mode() -> bool:
    return str(plugin_settings.FILLY_MOCK).strip() == "1"


@receiver(setting_changed)
def reload_plugin_settings(*args, **kwargs) -> None:
    setting = kwargs["setting"]
    if setting == "PLUGIN_CONFIGS":
        plugin_settings.reload()
