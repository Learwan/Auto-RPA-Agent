from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(StrEnum):
    CREATE_SESSION = "create_session"
    DELETE_SESSION = "delete_session"
    CREATE_AUTOMATION = "create_automation"
    EDIT_AUTOMATION = "edit_automation"
    DELETE_AUTOMATION = "delete_automation"
    EXECUTE_AUTOMATION = "execute_automation"
    VIEW_AUDIT_LOG = "view_audit_log"
    MANAGE_USERS = "manage_users"
    MANAGE_CREDENTIALS = "manage_credentials"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.EDITOR: {
        Permission.CREATE_SESSION,
        Permission.CREATE_AUTOMATION,
        Permission.EDIT_AUTOMATION,
        Permission.EXECUTE_AUTOMATION,
    },
    Role.VIEWER: {
        Permission.VIEW_AUDIT_LOG,
    },
}


@dataclass
class User:
    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    username: str = ""
    role: Role = Role.VIEWER
    active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def has_permission(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())


class RBACManager:
    def __init__(self):
        self._users: dict[str, User] = {}

    def create_user(self, username: str, role: Role = Role.VIEWER) -> User:
        user = User(username=username, role=role)
        self._users[user.user_id] = user
        logger.info("Created user: %s with role: %s", username, role.value)
        return user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def find_user_by_username(self, username: str) -> User | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    def update_role(self, user_id: str, role: Role) -> User | None:
        user = self._users.get(user_id)
        if user:
            user.role = role
            logger.info("Updated role for user %s to %s", user.username, role.value)
        return user

    def check_permission(self, user_id: str, permission: Permission) -> bool:
        user = self._users.get(user_id)
        if user is None or not user.active:
            return False
        return user.has_permission(permission)

    def list_users(self) -> list[User]:
        return list(self._users.values())

    def deactivate_user(self, user_id: str) -> bool:
        user = self._users.get(user_id)
        if user:
            user.active = False
            return True
        return False
