from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class AuditAction(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    LOGIN = "login"
    LOGOUT = "logout"
    EXPORT = "export"
    IMPORT = "import"


class AuditResource(StrEnum):
    SESSION = "session"
    AUTOMATION = "automation"
    EXECUTION = "execution"
    USER = "user"
    CREDENTIAL = "credential"
    SYSTEM = "system"


@dataclass
class AuditEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    user_id: str = ""
    action: AuditAction = AuditAction.READ
    resource: AuditResource = AuditResource.SYSTEM
    resource_id: str = ""
    details: str = ""
    ip_address: str = ""
    success: bool = True
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "action": self.action.value,
            "resource": self.resource.value,
            "resource_id": self.resource_id,
            "details": self.details,
            "ip_address": self.ip_address,
            "success": self.success,
            "metadata": self.metadata,
        }


class AuditLogger:
    def __init__(self):
        self._entries: list[AuditEntry] = []
        self._max_entries = 100000

    def log(
        self,
        user_id: str,
        action: AuditAction,
        resource: AuditResource,
        resource_id: str = "",
        details: str = "",
        ip_address: str = "",
        success: bool = True,
        metadata: dict | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            user_id=user_id,
            action=action,
            resource=resource,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            success=success,
            metadata=metadata or {},
        )
        self._entries.append(entry)

        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]

        logger.info(
            "Audit: user=%s action=%s resource=%s/%s success=%s",
            user_id,
            action.value,
            resource.value,
            resource_id,
            success,
        )
        return entry

    def query(
        self,
        user_id: str | None = None,
        action: AuditAction | None = None,
        resource: AuditResource | None = None,
        resource_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        results = self._entries
        if user_id:
            results = [e for e in results if e.user_id == user_id]
        if action:
            results = [e for e in results if e.action == action]
        if resource:
            results = [e for e in results if e.resource == resource]
        if resource_id:
            results = [e for e in results if e.resource_id == resource_id]
        if start_time:
            results = [e for e in results if e.timestamp >= start_time]
        if end_time:
            results = [e for e in results if e.timestamp <= end_time]
        return results[-limit:]

    def export_entries(self, limit: int = 10000) -> list[dict]:
        return [e.to_dict() for e in self._entries[-limit:]]
