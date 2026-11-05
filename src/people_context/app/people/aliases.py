"""Add one normalized-deduplicated alias to an existing person."""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from people_context.app._mutation import (
    audit_mutation,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.person import (
    ALIAS_EMAIL_NOT_HANDLE,
    Alias,
    AliasKind,
    Person,
    check_alias_kind,
    is_email_shaped,
)
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

    @model_validator(mode="after")
    def _email_is_a_handle(self) -> AddAliasInput:
        check_alias_kind(self.value, self.kind)
        return self


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


class AliasNotFoundError(Exception):
    """Raised when an alias edit names a value the person does not carry as an alias."""

    def __init__(self, person_id: str, value: str) -> None:
        self.person_id = person_id
        self.value = value
        super().__init__(f"alias not found for person {person_id}: {value}")


class AliasConflictError(Exception):
    """Raised when an alias edit would touch the canonical name or duplicate another name."""

    def __init__(self, person_id: str, value: str, reason: str) -> None:
        self.person_id = person_id
        self.value = value
        self.reason = reason
        super().__init__(f"alias change refused for person {person_id} ({reason}): {value}")


class RemoveAliasInput(BaseModel):
    """Input for removing one alias, named by value, from a known person."""

    person_id: str
    value: str
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RemoveAliasResult(BaseModel):
    """Concise outcome: which alias was targeted and whether it was present."""

    person_id: str
    alias: str
    removed: bool


class UpdateAliasInput(BaseModel):
    """Input for changing one alias in place; omitted fields stay unchanged."""

    person_id: str
    value: str
    new_value: str | None = None
    kind: AliasKind | None = None
    lang: str | None = None
    script: str | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class UpdateAliasResult(BaseModel):
    """Concise outcome: the alias after the change and which fields changed."""

    person_id: str
    alias: Alias
    changed: list[str]


_ALIAS_FIELDS = ("value", "kind", "lang", "script")


def _find_alias(person: Person, value: str) -> Alias | None:
    """Return the alias matching by normalized value; add/merge keep that form unique per person."""
    target = normalize_name(value)
    if target == normalize_name(person.canonical_name):
        raise AliasConflictError(person.id, value, "canonical_name")
    return next((alias for alias in person.aliases if normalize_name(alias.value) == target), None)


class RemoveAlias:
    """Drop one alias from an existing person, leaving every other record intact."""

    def __init__(self, people: PersonReader, writer: PersonWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RemoveAliasInput) -> RemoveAliasResult:
        """Remove the alias if present; an absent alias is a no-op reported as `removed: false`."""
        person = require_active_person(self._people, data.person_id)
        alias = _find_alias(person, data.value)
        if alias is not None:
            person.aliases.remove(alias)
            person.updated_at = self._clock.now()
            self._writer.save_person(person)
        removed = alias is not None
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="person",
            entity_id=person.id,
            payload={"alias_removed": data.value, "removed": removed},
            replay_payload=snapshot(person),
            changed_fields=["aliases", "updated_at"] if removed else [],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return RemoveAliasResult(person_id=person.id, alias=alias.value if alias else data.value, removed=removed)


class UpdateAlias:
    """Change one alias's value or metadata in place, keeping its id."""

    def __init__(self, people: PersonReader, writer: PersonWriter, audit: AuditLog, clock: Clock) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: UpdateAliasInput) -> UpdateAliasResult:
        """Apply the given fields; a new value may not collide with any other name of the person."""
        person = require_active_person(self._people, data.person_id)
        alias = _find_alias(person, data.value)
        if alias is None:
            raise AliasNotFoundError(person.id, data.value)
        before = alias.model_copy()
        if data.new_value is not None:
            others = {normalize_name(name) for name in person.all_names() if name != alias.value}
            if normalize_name(data.new_value) in others:
                raise AliasConflictError(person.id, data.new_value, "duplicate_name")
        # shortcut: None means "unchanged", so lang/script cannot be cleared; add a sentinel if that is needed.
        requested = {"value": data.new_value, "kind": data.kind, "lang": data.lang, "script": data.script}
        after = before.model_copy(update={field: v for field, v in requested.items() if v is not None})
        # The result, not the request, is what must hold: an email kept as `other` is refused even
        # when only its language changed, and setting `kind` to `handle` is how such an alias is fixed.
        if after.kind is not AliasKind.HANDLE and is_email_shaped(after.value):
            raise AliasConflictError(person.id, after.value, ALIAS_EMAIL_NOT_HANDLE)
        for field in _ALIAS_FIELDS:
            setattr(alias, field, getattr(after, field))
        changed = [field for field in _ALIAS_FIELDS if getattr(before, field) != getattr(alias, field)]
        if changed:
            person.updated_at = self._clock.now()
            self._writer.save_person(person)
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="person",
            entity_id=person.id,
            payload={"alias_before": before.model_dump(mode="json"), "alias_after": alias.model_dump(mode="json")},
            replay_payload=snapshot(person),
            changed_fields=["aliases", "updated_at"] if changed else [],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return UpdateAliasResult(person_id=person.id, alias=alias, changed=changed)
