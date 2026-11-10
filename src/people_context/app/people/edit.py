"""Explicit person identity curation."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from people_context.app._mutation import (
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.person import Alias, AliasKind, OptionalName, Person, check_person_name, kind_for_name
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader, PersonWriter


class PersonNameCollisionError(Exception):
    """Raised when an edit would duplicate another active person's name or alias."""

    def __init__(self, name: str, person_id: str) -> None:
        self.name = name
        self.person_id = person_id
        super().__init__(f"name already belongs to another person: {name}")


class EditPersonInput(BaseModel):
    """Optional person fields exposed by CLI curation."""

    person_id: str
    name: str | None = None
    preferred_name: OptionalName = None
    given_name: OptionalName = None
    family_name: OptionalName = None
    family_name_first: bool | None = None
    summary: str | None = None
    source: str = "cli"

    @field_validator("name")
    @classmethod
    def _name_is_not_an_email(cls, value: str | None) -> str | None:
        if value is not None:
            check_person_name(value)
        return value


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
        name_fields = [
            field for field in ("preferred_name", "given_name", "family_name") if field in data.model_fields_set
        ]
        fields.extend(name_fields)
        if data.family_name_first is not None:
            fields.append("family_name_first")
        if not fields:
            raise ValueError("at least one person field is required")
        before = snapshot(person)
        if data.name is not None:
            normalized = normalize_name(data.name)
            collision = next(
                (
                    candidate
                    for candidate in self._people.find_by_normalized_name(normalized)
                    if candidate.id != person.id
                ),
                None,
            )
            if collision is not None:
                raise PersonNameCollisionError(data.name, collision.id)
            _keep_former_name(person, normalized)
            person.canonical_name = data.name
        if data.family_name_first is not None:
            person.family_name_first = data.family_name_first
        for field in name_fields:
            setattr(person, field, getattr(data, field))
        if data.summary is not None:
            person.summary = data.summary
        if before["aliases"] != snapshot(person)["aliases"]:
            fields.append("aliases")
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


def _keep_former_name(person: Person, new_normalized: str) -> None:
    """Keep the name being replaced as an alias, so a rename never loses how someone was known.

    An email address stored as a name before that was refused becomes a `handle`, which keeps it as
    contact information and keeps import matching on it. The new name stops being an alias.
    """
    old = person.canonical_name
    person.aliases = [alias for alias in person.aliases if normalize_name(alias.value) != new_normalized]
    old_normalized = normalize_name(old)
    if old_normalized == new_normalized or any(normalize_name(a.value) == old_normalized for a in person.aliases):
        return
    person.aliases.append(Alias(value=old, kind=kind_for_name(old, AliasKind.FORMER_NAME)))
