"""Record a derived trait about an existing person."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.shared import Confidence, Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class RecordTraitInput(BaseModel):
    """Input for a derived trait assertion."""

    person_id: str
    category: TraitCategory
    value: str
    evidence_note: str | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordTrait:
    """Create one provenanced trait for a known person."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RecordTraitInput) -> Trait:
        """Persist and audit a validated trait category."""
        require_active_person(self._people, data.person_id)
        trait = Trait(
            person_id=data.person_id,
            category=data.category,
            value=data.value,
            evidence_note=data.evidence_note,
            confidence=data.confidence if data.confidence is not None else 1.0,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
            updated_at=self._clock.now(),
        )
        self._writer.save_trait(trait)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="trait",
            entity_id=trait.id,
            payload=snapshot(trait),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return trait
