from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerType(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    EVENT = "event"
    MANUAL = "manual"


@dataclass
class ScheduleEntry:
    schedule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    automation_id: str = ""
    name: str = ""
    trigger_type: TriggerType = TriggerType.MANUAL
    cron_expression: str = ""
    interval_seconds: int = 0
    event_pattern: str = ""
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    last_run: str | None = None
    next_run: str | None = None
    run_count: int = 0
    failure_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    variables: dict = field(default_factory=dict)
    notification_webhook: str = ""
    notification_email: str = ""

    def to_dict(self) -> dict:
        return {
            "schedule_id": self.schedule_id,
            "automation_id": self.automation_id,
            "name": self.name,
            "trigger_type": self.trigger_type.value,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "status": self.status.value,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "created_at": self.created_at,
        }


class SchedulerService:
    def __init__(self):
        self._schedules: dict[str, ScheduleEntry] = {}

    def create_schedule(
        self,
        automation_id: str,
        name: str,
        trigger_type: TriggerType = TriggerType.MANUAL,
        cron_expression: str = "",
        interval_seconds: int = 0,
        variables: dict | None = None,
        notification_webhook: str = "",
        notification_email: str = "",
    ) -> ScheduleEntry:
        entry = ScheduleEntry(
            automation_id=automation_id,
            name=name,
            trigger_type=trigger_type,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            variables=variables or {},
            notification_webhook=notification_webhook,
            notification_email=notification_email,
        )
        self._schedules[entry.schedule_id] = entry
        logger.info("Created schedule: %s for automation: %s", name, automation_id)
        return entry

    def get_schedule(self, schedule_id: str) -> ScheduleEntry | None:
        return self._schedules.get(schedule_id)

    def list_schedules(self, automation_id: str | None = None) -> list[ScheduleEntry]:
        schedules = list(self._schedules.values())
        if automation_id:
            schedules = [s for s in schedules if s.automation_id == automation_id]
        return schedules

    def update_schedule(self, schedule_id: str, **kwargs) -> ScheduleEntry | None:
        entry = self._schedules.get(schedule_id)
        if entry is None:
            return None
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        logger.info("Updated schedule: %s", schedule_id)
        return entry

    def pause_schedule(self, schedule_id: str) -> ScheduleEntry | None:
        return self.update_schedule(schedule_id, status=ScheduleStatus.PAUSED)

    def resume_schedule(self, schedule_id: str) -> ScheduleEntry | None:
        return self.update_schedule(schedule_id, status=ScheduleStatus.ACTIVE)

    def delete_schedule(self, schedule_id: str) -> bool:
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            logger.info("Deleted schedule: %s", schedule_id)
            return True
        return False

    def record_run(self, schedule_id: str, success: bool) -> None:
        entry = self._schedules.get(schedule_id)
        if entry:
            entry.last_run = datetime.now(UTC).isoformat()
            entry.run_count += 1
            if not success:
                entry.failure_count += 1
