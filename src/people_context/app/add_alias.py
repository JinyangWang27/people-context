"""Add one normalized-deduplicated alias to an existing person."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app.write_support import (
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader, PersonWriter


class AddAliasInput(BaseModel):
    """Input for adding an alias to a known person."""

    person_id: str
    value: str
    kind: AliasKind = AliasKind.OTHER
    lang: str | None = None
    script: str | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class AddAlias:
    """Merge an alias into an existing person's names."""

    def __init__(self, people: PersonReader, writer: PersonWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: AddAliasInput) -> Person:
        """Return person with alias present exactly once by normalized value."""
        person = require_active_person(self._people, data.person_id)
        normalized_names = {normalize_name(name) for name in person.all_names()}
        added = normalize_name(data.value) not in normalized_names
        if added:
            person.aliases.append(Alias(value=data.value, kind=data.kind, lang=data.lang, script=data.script))
            person.updated_at = self._clock.now()
            self._writer.save_person(person)
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="person",
            entity_id=person.id,
            payload={"alias": data.value, "added": added},
            replay_payload=snapshot(person),
            changed_fields=["aliases", "updated_at"] if added else [],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return person
