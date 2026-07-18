"""Narrow ports for atomic lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from people_context.domain.person import Person


class LifecycleTargetNotFoundError(Exception):
    """Raised by a lifecycle adapter when an atomic target does not exist."""


@dataclass(frozen=True)
class LifecycleChange:
    """One exact row outcome produced inside a lifecycle transaction."""

    entity_type: str
    entity_id: str
    op_kind: str
    payload: dict[str, Any]
    changed_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AffectedEntity:
    """Stable identity of a row or join-row affected by forget."""

    entity_type: str
    entity_id: str


@dataclass(frozen=True)
class ForgetStoreResult:
    """Local deletion/redaction outcome used to construct a safe tombstone."""

    deleted: dict[str, int]
    affected_entities: list[AffectedEntity]
    covered_op_ids: list[str]
    covered_transaction_ids: list[str]


@dataclass(frozen=True)
class MergeStoreResult:
    """Exact local effects and user-facing counts from one merge."""

    counts: dict[str, int]
    changes: list[LifecycleChange]
    manifest: dict[str, Any]


@runtime_checkable
class LifecycleStore(Protocol):
    """Persist multi-row lifecycle mutations atomically."""

    def merge_people(self, primary: Person, duplicate_id: str) -> MergeStoreResult: ...

    def forget_person(self, person_id: str) -> ForgetStoreResult: ...

    def forget_record(self, entity_type: str, entity_id: str) -> ForgetStoreResult: ...

    def preview_person_forget(self, person_id: str) -> dict[str, int]: ...
