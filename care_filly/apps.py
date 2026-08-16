from django.apps import AppConfig
from django.core import checks
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "care_filly"


@checks.register()
def check_medispeak_credentials(app_configs, **kwargs):
    """Warn — never error — when the Medispeak credentials are unset.

    This must stay at Warning level. An Error-level check raises
    SystemCheckError, which would fail ``manage.py migrate`` and
    ``collectstatic`` on any box without credentials (CI, the release phase
    of a deploy), reintroducing exactly the import-time failure that keeping
    REQUIRED_SETTINGS empty avoids.
    """
    from care_filly.settings import plugin_settings

    if plugin_settings.MEDISPEAK_BASE_URL and plugin_settings.MEDISPEAK_API_KEY:
        return []
    return [
        checks.Warning(
            "MEDISPEAK_BASE_URL / MEDISPEAK_API_KEY are not configured; "
            "/api/care_filly/v1/medispeak/* will return 502.",
            hint=(
                "Set them in the care_filly plugin config (PLUGIN_CONFIGS) or "
                "as environment variables."
            ),
            id="care_filly.W001",
        )
    ]


class CareFillyConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("CARE Filly Backend")

    def ready(self):
        from care.security.permissions.base import PermissionController

        # Importing the security module registers FillyAccess with the
        # AuthorizationController (see care_filly.security.authorization).
        from care_filly.security import FillyAccess, FillyPermissions  # noqa: F401

        PermissionController.register_permission_handler(FillyPermissions)
