"""Narrow ports for atomic lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
