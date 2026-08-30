"""Audit-log port: append-only changelog of mutations.

Payload convention: one row mutation produces one entry. Create payloads describe
the resulting row; ordinary updates describe changed values; corrections and
status transitions use ``before``, ``after``, and sorted ``fields`` keys. Payloads
must be JSON-compatible and omit secret/full preference text where a concise
summary is sufficient.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id

#: Operations this release's write use cases record. `op` stays a plain `str` because a
#: restored bundle carries whatever its origin wrote, including an operation a later release
#: introduced; this set is what a reader can rely on recognizing, not a validation rule.
KNOWN_AUDIT_OPERATIONS: frozenset[str] = frozenset(
    {"correct", "create", "delete", "forget", "merge", "supersede", "update"}
)


class AuditEntry(BaseModel):
    """A single append-only audit record."""

    id: str = Field(default_factory=new_id)
    ts: datetime
    op: str  # one of KNOWN_AUDIT_OPERATIONS when written here; free-form when restored
    entity_type: str  # "person" | "fact" | ...
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str  # provenance source string


@runtime_checkable
class AuditLog(Protocol):
    """Append-only audit log."""

    def append(self, entry: AuditEntry) -> None: ...

    def list_entries(self, limit: int = 100) -> list[AuditEntry]: ...
