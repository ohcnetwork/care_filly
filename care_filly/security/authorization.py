from care.security.authorization.base import (
    AuthorizationController,
    AuthorizationHandler,
)

from care_filly.security.permissions import FillyPermissions


class FillyAccess(AuthorizationHandler):
    """Authorization logic for filly sessions, quota and history."""

    def can_use_filly(self, user, facility) -> bool:
        return self.check_permission_in_facility_organization(
            [FillyPermissions.can_use_filly.name], user, facility=facility
        )

    def can_view_filly_session(self, user, session) -> bool:
        """SDK session access is owner-only (or superuser)."""
        if user.is_superuser:
            return True
        return session.user_id == user.id

    def can_view_filly_history(self, user, facility) -> bool:
        return self.check_permission_in_facility_organization(
            [FillyPermissions.can_view_filly_history.name], user, facility=facility
        )

    def can_manage_filly_quota(self, user, facility) -> bool:
        return self.check_permission_in_facility_organization(
            [FillyPermissions.can_manage_filly_quota.name], user, facility=facility
        )

    def get_filly_history(self, user, base_qs):
        """Users only ever see their own sessions."""
        return base_qs.filter(user=user)


AuthorizationController.register_internal_controller(FillyAccess)
