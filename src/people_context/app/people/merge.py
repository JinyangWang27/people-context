"""Atomic duplicate-person merge orchestration."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.app._mutation import (
    audit_mutation,
    changelog_mutation,
    require_active_person,
    transactional,
    unit_of_work_for,
)
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import new_id, normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.merge import MergeStore
from people_context.ports.repository import PersonReader


class MergePeopleError(Exception):
    """Raised when a merge direction is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MergeMovedCounts(BaseModel):
    """Counts of duplicate-linked rows processed by a merge."""

    facts: int = 0
    observations: int = 0
    traits: int = 0
    reminders: int = 0
    affiliations: int = 0
    relationships: int = 0
    interaction_participations: int = 0


class MergePeopleResult(BaseModel):
    """Merged primary person and atomic row counts."""

    person: Person
    moved: MergeMovedCounts
    self_loops_removed: int
    duplicate_relationships_removed: int = 0


class MergePeople:
    """Validate and merge a duplicate person into an active primary person."""

    def __init__(
        self,
        people: PersonReader,
        lifecycle: MergeStore,
        clock: Clock,
        audit: AuditLog | None = None,
    ) -> None:
        self._people = people
        self._lifecycle = lifecycle
        self._clock = clock
        self._audit = audit or lifecycle.audit_log
        self._uow = unit_of_work_for(lifecycle, self._audit)

    @transactional
    def execute(self, primary_id: str, duplicate_id: str, source: str = "agent") -> MergePeopleResult:
        """Merge aliases, summary, and all linked records in one store transaction."""
        if primary_id == duplicate_id:
            raise MergePeopleError("same_person", "primary and duplicate must be different people")
        primary = require_active_person(self._people, primary_id).model_copy(deep=True)
        duplicate = require_active_person(self._people, duplicate_id)
        if duplicate.is_self and not primary.is_self:
            raise MergePeopleError("self_merge_direction", "the self person must be the primary merge target")

        aliases_added = self._merge_identity(primary, duplicate)
        primary.updated_at = self._clock.now()
        transaction_id = new_id()
        store_result = self._lifecycle.merge_people(primary, duplicate.id)
        counts = store_result.counts
        for change in store_result.changes:
            changelog_mutation(
                self._audit,
                self._clock,
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                op_kind=change.op_kind,
                payload=change.payload,
                changed_fields=change.changed_fields,
                transaction_id=transaction_id,
                source=source,
            )
        summary_keys = {"self_loops_removed", "duplicate_relationships_removed"}
        moved_payload = {key: value for key, value in counts.items() if key not in summary_keys}
        audit_payload = {
            "duplicate_id": duplicate.id,
            "aliases_added": aliases_added,
            "moved": moved_payload,
            "self_loops_removed": counts["self_loops_removed"],
            "duplicate_relationships_removed": counts.get("duplicate_relationships_removed", 0),
        }
        audit_mutation(
            self._audit,
            self._clock,
            op="merge",
            entity_type="person",
            entity_id=primary.id,
            payload=audit_payload,
            replay_payload=store_result.manifest,
            changed_fields=[],
            transaction_id=transaction_id,
            source=source,
        )
        moved = MergeMovedCounts.model_validate(moved_payload)
        return MergePeopleResult(
            person=primary,
            moved=moved,
            self_loops_removed=counts["self_loops_removed"],
            duplicate_relationships_removed=counts.get("duplicate_relationships_removed", 0),
        )

    @staticmethod
    def _merge_identity(primary: Person, duplicate: Person) -> list[str]:
        known = {normalize_name(name) for name in primary.all_names()}
        candidates = [Alias(value=duplicate.canonical_name, kind=AliasKind.FORMER_NAME), *duplicate.aliases]
        aliases_added: list[str] = []
        for alias in candidates:
            normalized = normalize_name(alias.value)
            if not normalized or normalized in known:
                continue
            known.add(normalized)
            primary.aliases.append(alias.model_copy(deep=True))
            aliases_added.append(alias.value)
        if not primary.summary:
            primary.summary = duplicate.summary
        return aliases_added
