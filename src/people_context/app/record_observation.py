"""Record a subjective observation about an existing person."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from people_context.app.write_support import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.observation import Observation
from people_context.domain.shared import Sensitivity
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class RecordObservationInput(BaseModel):
    """Input for a subjective observation."""

    person_id: str
    text: str
    observed_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordObservation:
    """Create one provenanced observation for a known person."""

    def __init__(self, people: PersonReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RecordObservationInput) -> Observation:
        """Persist and audit an observation."""
        require_active_person(self._people, data.person_id)
        observation = Observation(
            person_id=data.person_id,
            text=data.text,
            observed_at=data.observed_at or self._clock.now(),
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
        )
        self._writer.save_observation(observation)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="observation",
            entity_id=observation.id,
            payload=snapshot(observation),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return observation
