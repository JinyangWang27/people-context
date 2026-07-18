"""Plan or apply canonical rewrites for legacy relationship rows."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from people_context.app.relationship_policy import normalize_relationship
from people_context.app.write_support import audit_mutation, snapshot, transactional, unit_of_work_for
from people_context.domain.relationship import Relationship
from people_context.domain.shared import ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.relationship_vocabulary import RelationshipStore, RelationshipVocabularyReader


class RelationshipNormalizationChange(BaseModel):
    """One deterministic normalization action."""

    action: str
    relationship_id: str
    before: Relationship
    after: Relationship | None = None
    merged_into: str | None = None


class NormalizeRelationshipsResult(BaseModel):
    """Dry-run/apply report."""

    applied: bool
    changes: list[RelationshipNormalizationChange] = Field(default_factory=list)


class NormalizeRelationships:
    """Canonicalize legacy edges without collapsing disjoint relationship history."""

    def __init__(
        self,
        store: RelationshipStore,
        vocabulary: RelationshipVocabularyReader,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._store = store
        self._vocabulary = vocabulary
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    def execute(self, *, apply: bool = False, source: str = "cli") -> NormalizeRelationshipsResult:
        changes = self._plan()
        if apply and changes:
            self._apply(changes, source)
        return NormalizeRelationshipsResult(applied=apply, changes=changes)

    def _plan(self) -> list[RelationshipNormalizationChange]:
        today = self._clock.now().date()
        rows = sorted(
            self._store.list_relationships(),
            key=lambda row: (not _is_active(row.period, today), row.created_at, row.id),
        )
        keepers: dict[tuple[str, str, str], list[Relationship]] = {}
        changes: list[RelationshipNormalizationChange] = []
        for row in rows:
            normalized = normalize_relationship(row.subject_id, row.object_id, row.type, self._vocabulary)
            after = row.model_copy(
                update={
                    "subject_id": normalized.subject_id,
                    "object_id": normalized.object_id,
                    "type": normalized.type,
                }
            )
            key = (after.subject_id, after.object_id, after.type)
            matching_keepers = keepers.setdefault(key, [])
            keeper = next(
                (candidate for candidate in matching_keepers if _periods_overlap(candidate.period, after.period)),
                None,
            )
            if keeper is not None:
                changes.append(
                    RelationshipNormalizationChange(
                        action="merge",
                        relationship_id=row.id,
                        before=row,
                        merged_into=keeper.id,
                    )
                )
                continue
            matching_keepers.append(after)
            if after != row:
                changes.append(
                    RelationshipNormalizationChange(
                        action="update",
                        relationship_id=row.id,
                        before=row,
                        after=after,
                    )
                )
        return changes

    @transactional
    def _apply(self, changes: list[RelationshipNormalizationChange], source: str) -> None:
        for change in changes:
            if change.action == "update" and change.after is not None:
                self._store.save_relationship(change.after)
                audit_mutation(
                    self._audit,
                    self._clock,
                    op="update",
                    entity_type="relationship",
                    entity_id=change.relationship_id,
                    payload={"before": snapshot(change.before), "after": snapshot(change.after)},
                    replay_payload=snapshot(change.after),
                    changed_fields=["subject_id", "object_id", "type"],
                    source=source,
                )
            elif change.action == "merge" and change.merged_into is not None:
                self._store.delete_relationship(change.relationship_id)
                audit_mutation(
                    self._audit,
                    self._clock,
                    op="delete",
                    entity_type="relationship",
                    entity_id=change.relationship_id,
                    payload={"removed": snapshot(change.before), "merged_into": change.merged_into},
                    replay_payload={"id": change.relationship_id, "merged_into": change.merged_into},
                    changed_fields=["deleted"],
                    source=source,
                )


def _periods_overlap(left: ValidityPeriod, right: ValidityPeriod) -> bool:
    left_start = left.valid_from or date.min
    right_start = right.valid_from or date.min
    left_end = left.valid_to or date.max
    right_end = right.valid_to or date.max
    return max(left_start, right_start) <= min(left_end, right_end)


def _is_active(period: ValidityPeriod, as_of: date) -> bool:
    return (period.valid_from is None or period.valid_from <= as_of) and (
        period.valid_to is None or period.valid_to >= as_of
    )
