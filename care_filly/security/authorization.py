from care.security.authorization.base import (
    AuthorizationController,
    AuthorizationHandler,
)

from care_filly.security.permissions import FillyPermissions


class FillyAccess(AuthorizationHandler):
    """Authorization logic for filly sessions."""

    def can_use_filly(self, user, facility) -> bool:
        return self.check_permission_in_facility_organization(
            [FillyPermissions.can_use_filly.name], user, facility=facility
        )


AuthorizationController.register_internal_controller(FillyAccess)
