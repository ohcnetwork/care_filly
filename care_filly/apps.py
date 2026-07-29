from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

PLUGIN_NAME = "care_filly"


class CareFillyConfig(AppConfig):
    name = PLUGIN_NAME
    verbose_name = _("CARE Filly Backend")

    def ready(self):
        from care.security.permissions.base import PermissionController

        # Importing the security module registers FillyAccess with the
        # AuthorizationController (see care_filly.security.authorization).
        from care_filly.security import FillyAccess, FillyPermissions  # noqa: F401

        PermissionController.register_permission_handler(FillyPermissions)
