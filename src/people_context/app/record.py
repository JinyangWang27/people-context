"""Remember-person use case: create or update a person from user/agent input."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.app.write_support import audit_mutation, snapshot, transactional, unit_of_work_for
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader, PersonWriter


class AmbiguousPersonError(Exception):
    """Raised when a normalized name matches more than one existing person."""

    def __init__(self, candidates: list[Person]) -> None:
        self.person_ids = [p.id for p in candidates]
        self.names = [p.canonical_name for p in candidates]
        super().__init__(f"normalized name matches multiple persons: {', '.join(self.names)}")


class SelfAlreadyExistsError(Exception):
    """Raised when marking a new person as self while a different self already exists."""

    def __init__(self, existing: Person) -> None:
        self.existing_id = existing.id
        self.existing_name = existing.canonical_name
        super().__init__(f"a different person is already marked self: {existing.canonical_name}")


class AliasInput(BaseModel):
    """An alias to attach to a remembered person."""

    value: str
    kind: AliasKind = AliasKind.OTHER
    lang: str | None = None
    script: str | None = None


class RememberPersonInput(BaseModel):
    """Input payload describing a person to create or update."""

    name: str
    aliases: list[AliasInput] = Field(default_factory=list)
    summary: str | None = None
    is_self: bool = False
    source: str = "user"
    session: str | None = None


class RememberPersonResult(BaseModel):
    """Outcome of a remember operation."""

    person: Person
    created: bool


class RememberPerson:
    """Create a new person or update the existing one matching the given name."""

    def __init__(self, reader: PersonReader, writer: PersonWriter, audit: AuditLog, clock: Clock) -> None:
        self._reader = reader
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RememberPersonInput) -> RememberPersonResult:
        """Upsert a person by normalized name, recording an audit entry."""
        normalized = normalize_name(data.name)
        matches = self._reader.find_by_normalized_name(normalized)
        if len(matches) > 1:
            raise AmbiguousPersonError(matches)

        if data.is_self:
            self._guard_single_self(matches)

        if matches:
            return self._update(matches[0], data)
        return self._create(data)

    def _guard_single_self(self, matches: list[Person]) -> None:
        existing_self = self._reader.get_self()
        if existing_self is None:
            return
        target_id = matches[0].id if matches else None
        if existing_self.id != target_id:
            raise SelfAlreadyExistsError(existing_self)

    def _update(self, person: Person, data: RememberPersonInput) -> RememberPersonResult:
        before = snapshot(person)
        known = {normalize_name(name) for name in person.all_names()}
        added = 0
        for alias in data.aliases:
            key = normalize_name(alias.value)
            if key in known:
                continue
            known.add(key)
            person.aliases.append(Alias(value=alias.value, kind=alias.kind, lang=alias.lang, script=alias.script))
            added += 1

        if data.summary is not None:
            person.summary = data.summary
        if data.is_self:
            person.is_self = True
        person.updated_at = self._clock.now()

        self._writer.save_person(person)
        after = snapshot(person)
        changed_fields = sorted(key for key in after if after[key] != before[key])
        self._append_audit(
            person,
            op="update",
            created=False,
            alias_count=added,
            source=data.source,
            session=data.session,
            changed_fields=changed_fields,
        )
        return RememberPersonResult(person=person, created=False)

    def _create(self, data: RememberPersonInput) -> RememberPersonResult:
        now = self._clock.now()
        aliases = [
            Alias(value=alias.value, kind=alias.kind, lang=alias.lang, script=alias.script) for alias in data.aliases
        ]
        person = Person(
            canonical_name=data.name,
            is_self=data.is_self,
            summary=data.summary,
            aliases=aliases,
            created_at=now,
            updated_at=now,
        )
        self._writer.save_person(person)
        self._append_audit(
            person,
            op="create",
            created=True,
            alias_count=len(aliases),
            source=data.source,
            session=data.session,
            changed_fields=[],
        )
        return RememberPersonResult(person=person, created=True)

    def _append_audit(
        self,
        person: Person,
        *,
        op: str,
        created: bool,
        alias_count: int,
        source: str,
        session: str | None,
        changed_fields: list[str],
    ) -> None:
        audit_mutation(
            self._audit,
            self._clock,
            op=op,
            entity_type="person",
            entity_id=person.id,
            payload={"canonical_name": person.canonical_name, "created": created, "alias_count": alias_count},
            replay_payload=snapshot(person),
            changed_fields=changed_fields,
            source=source,
            session=session,
        )
