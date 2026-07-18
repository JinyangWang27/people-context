"""Record a time-aware fact about an existing person."""

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
from people_context.domain.fact import Fact
from people_context.domain.shared import Confidence, Sensitivity, ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class RecordFactInput(BaseModel):
    """Input for a factual assertion."""

    person_id: str
    predicate: str
    value: str
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordFact:
    """Create one provenanced fact for a known person."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RecordFactInput) -> Fact:
        """Persist and audit a fact."""
        require_active_person(self._people, data.person_id)
        fact = Fact(
            person_id=data.person_id,
            predicate=data.predicate,
            value=data.value,
            period=ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to),
            recorded_at=self._clock.now(),
            confidence=data.confidence if data.confidence is not None else 1.0,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
        )
        self._writer.save_fact(fact)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="fact",
            entity_id=fact.id,
            payload=snapshot(fact),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return fact
