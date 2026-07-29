import enum

from care.security.permissions.constants import Permission, PermissionContext
from care.security.roles.role import (
    ADMIN_ROLE,
    ADMINISTRATOR,
    DOCTOR_ROLE,
    FACILITY_ADMIN_ROLE,
    NURSE_ROLE,
    STAFF_ROLE,
)

FILLY_USER_ROLES = [
    DOCTOR_ROLE,
    NURSE_ROLE,
    STAFF_ROLE,
    ADMIN_ROLE,
    FACILITY_ADMIN_ROLE,
    ADMINISTRATOR,
]

FILLY_ADMIN_ROLES = [
    ADMIN_ROLE,
    FACILITY_ADMIN_ROLE,
    ADMINISTRATOR,
]


class FillyPermissions(enum.Enum):
    can_use_filly = Permission(
        "Can Use Filly",
        "Allows recording an encounter and generating a filly draft",
        PermissionContext.FACILITY,
        FILLY_USER_ROLES,
    )
    can_view_filly_history = Permission(
        "Can View Filly History",
        "Allows viewing past filly sessions",
        PermissionContext.FACILITY,
        FILLY_USER_ROLES,
    )
    can_manage_filly_quota = Permission(
        "Can Manage Filly Quota",
        "Allows configuring filly token quotas for a facility",
        PermissionContext.FACILITY,
        FILLY_ADMIN_ROLES,
    )
