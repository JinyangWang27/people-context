"""Create a directed relationship between two known people."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from people_context.app.write_support import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.relationship import Relationship
from people_context.domain.shared import Confidence, ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class SetRelationshipInput(BaseModel):
    """Input for a directed relationship assertion."""

    subject_id: str
    object_id: str
    type: str
    label: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SetRelationship:
    """Persist a relationship only when both endpoints exist."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: SetRelationshipInput) -> Relationship:
        """Create and audit a directed relationship."""
        for person_id in (data.subject_id, data.object_id):
            require_active_person(self._people, person_id)
        relationship = Relationship(
            subject_id=data.subject_id,
            object_id=data.object_id,
            type=data.type,
            label=data.label,
            period=ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to),
            confidence=data.confidence if data.confidence is not None else 1.0,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._writer.save_relationship(relationship)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="relationship",
            entity_id=relationship.id,
            payload=snapshot(relationship),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return relationship
