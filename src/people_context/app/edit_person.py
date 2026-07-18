"""Explicit person identity curation."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import (
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader, PersonWriter


class PersonNameCollisionError(Exception):
    """Raised when an edit would duplicate another active canonical name."""

    def __init__(self, name: str, person_id: str) -> None:
        self.name = name
        self.person_id = person_id
        super().__init__(f"canonical name already belongs to another person: {name}")


class EditPersonInput(BaseModel):
    """Optional person fields exposed by CLI curation."""

    person_id: str
    name: str | None = None
    summary: str | None = None
    source: str = "cli"


class EditPerson:
    """Edit canonical identity fields and retain a full before/after audit."""

    def __init__(self, people: PersonReader, writer: PersonWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: EditPersonInput) -> Person:
        """Apply supplied fields to one active person."""
        person = require_active_person(self._people, data.person_id)
        fields = []
        if data.name is not None:
            fields.append("canonical_name")
        if data.summary is not None:
            fields.append("summary")
        if not fields:
            raise ValueError("at least one of name or summary is required")
        before = snapshot(person)
        if data.name is not None:
            normalized = normalize_name(data.name)
            collision = next(
                (
                    candidate
                    for candidate in self._people.list_people()
                    if candidate.id != person.id and normalize_name(candidate.canonical_name) == normalized
                ),
                None,
            )
            if collision is not None:
                raise PersonNameCollisionError(data.name, collision.id)
            person.canonical_name = data.name
        if data.summary is not None:
            person.summary = data.summary
        person.updated_at = self._clock.now()
        self._writer.save_person(person)
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="person",
            entity_id=person.id,
            payload={"before": before, "after": snapshot(person), "fields": fields},
            replay_payload=snapshot(person),
            changed_fields=[*fields, "updated_at"],
            source=data.source,
        )
        return person
