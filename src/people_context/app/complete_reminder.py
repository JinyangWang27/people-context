"""Complete one active reminder."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import (
    RecordNotFoundError,
    ReminderNotActiveError,
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordReader, RecordWriter
from people_context.ports.repository import PersonReader


class CompleteReminderInput(BaseModel):
    """Input for a reminder status transition."""

    reminder_id: str
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class CompleteReminder:
    """Transition an active reminder to completed and audit before/after."""

    def __init__(
        self,
        records: RecordReader,
        writer: RecordWriter,
        audit: AuditLog,
        clock: Clock,
        *,
        people: PersonReader,
    ) -> None:
        self._records = records
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)
        self._people = people

    @transactional
    def execute(self, data: CompleteReminderInput) -> Reminder:
        """Complete one reminder, rejecting repeated completion."""
        current = self._records.get_record("reminder", data.reminder_id)
        if not isinstance(current, Reminder):
            raise RecordNotFoundError("reminder", data.reminder_id)
        require_active_person(self._people, current.person_id)
        if current.status != ReminderStatus.ACTIVE:
            raise ReminderNotActiveError(current.id, current.status.value)
        before = snapshot(current)
        updated = self._writer.update_record_fields("reminder", current.id, {"status": ReminderStatus.COMPLETED})
        if not isinstance(updated, Reminder):
            raise RecordNotFoundError("reminder", data.reminder_id)
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="reminder",
            entity_id=updated.id,
            payload={"before": before, "after": snapshot(updated), "fields": ["status"]},
            replay_payload=snapshot(updated),
            changed_fields=["status"],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return updated
