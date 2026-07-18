"""Create a validated person-linked reminder."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from people_context.app.write_support import (
    InvalidReminderError,
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.reminder import Reminder, ReminderKind
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class SetReminderInput(BaseModel):
    """Input for a reminder with provenance used by its audit entry."""

    person_id: str
    text: str
    kind: ReminderKind
    due_at: datetime | None = None
    recurrence: str | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SetReminder:
    """Create reminders while enforcing kind-specific scheduling rules."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: SetReminderInput) -> Reminder:
        """Persist and audit a reminder."""
        require_active_person(self._people, data.person_id)
        if data.kind == ReminderKind.COMMUNICATION_NOTE and (data.due_at is not None or data.recurrence is not None):
            raise InvalidReminderError("communication_note reminders cannot have due_at or recurrence")
        if data.kind in (ReminderKind.FOLLOW_UP, ReminderKind.OCCASION) and data.due_at is None:
            raise InvalidReminderError(f"{data.kind.value} reminders require due_at")
        if data.kind != ReminderKind.OCCASION and data.recurrence is not None:
            raise InvalidReminderError("recurrence is only valid for occasion reminders")
        reminder = Reminder(
            person_id=data.person_id,
            text=data.text,
            kind=data.kind,
            due_at=data.due_at,
            recurrence=data.recurrence,
            created_at=self._clock.now(),
        )
        self._writer.save_reminder(reminder)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="reminder",
            entity_id=reminder.id,
            payload=snapshot(reminder),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return reminder
