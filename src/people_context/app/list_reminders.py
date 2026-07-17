"""List pull-based reminders with stable filtering and ordering."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.ports.records import RecordReader


class ListRemindersInput(BaseModel):
    """Optional reminder filters; active is the default lifecycle state."""

    person_id: str | None = None
    due_before: datetime | None = None
    status: ReminderStatus = ReminderStatus.ACTIVE


class ListReminders:
    """Read due reminders first, followed by undated communication notes."""

    def __init__(self, records: RecordReader) -> None:
        self._records = records

    def execute(self, data: ListRemindersInput) -> list[Reminder]:
        """Return reminders matching all filters in deterministic order."""
        return self._records.list_reminders(
            person_id=data.person_id,
            due_before=data.due_before,
            status=data.status,
        )
