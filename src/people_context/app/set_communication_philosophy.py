"""Store the user's communication philosophy without auditing its text."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import audit_mutation, transactional, unit_of_work_for
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY, CommunicationPhilosophy
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import PreferencesStore


class SetCommunicationPhilosophyInput(BaseModel):
    """Input for the free-text communication framework."""

    text: str
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SetCommunicationPhilosophy:
    """Upsert communication philosophy and audit lengths only."""

    def __init__(self, preferences: PreferencesStore, audit: AuditLog, clock: Clock) -> None:
        self._preferences = preferences
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: SetCommunicationPhilosophyInput) -> CommunicationPhilosophy:
        """Store text verbatim while excluding it from audit payloads."""
        previous = self._preferences.get(PREF_COMMUNICATION_PHILOSOPHY)
        self._preferences.set(PREF_COMMUNICATION_PHILOSOPHY, data.text)
        philosophy = CommunicationPhilosophy(text=data.text, updated_at=self._clock.now())
        audit_mutation(
            self._audit,
            self._clock,
            op="update" if previous is not None else "create",
            entity_type="preference",
            entity_id=PREF_COMMUNICATION_PHILOSOPHY,
            payload={"before_length": len(previous) if previous is not None else None, "after_length": len(data.text)},
            replay_payload={
                "key": PREF_COMMUNICATION_PHILOSOPHY,
                "value": data.text,
                "updated_at": philosophy.updated_at.isoformat(),
            },
            changed_fields=["value", "updated_at"],
            op_kind="prefs",
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return philosophy
