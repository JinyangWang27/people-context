"""Record a concise interaction summary among known participants."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.app.write_support import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.interaction import Interaction
from people_context.domain.shared import Sensitivity
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class RecordInteractionInput(BaseModel):
    """Input for a summary-only interaction."""

    summary: str
    participant_ids: list[str] = Field(min_length=1)
    occurred_at: datetime | None = None
    channel: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordInteraction:
    """Create one interaction after validating every participant."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RecordInteractionInput) -> Interaction:
        """Persist and audit a deduplicated-participant interaction."""
        participant_ids = list(dict.fromkeys(data.participant_ids))
        for person_id in participant_ids:
            require_active_person(self._people, person_id)
        interaction = Interaction(
            summary=data.summary,
            participant_ids=participant_ids,
            occurred_at=data.occurred_at or self._clock.now(),
            channel=data.channel,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
        )
        self._writer.save_interaction(interaction)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="interaction",
            entity_id=interaction.id,
            payload=snapshot(interaction),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return interaction
