"""Audit-log port: append-only changelog of mutations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id


class AuditEntry(BaseModel):
    """A single append-only audit record."""

    id: str = Field(default_factory=new_id)
    ts: datetime
    op: str  # "create" | "update" | "merge" | "forget" | ...
    entity_type: str  # "person" | "fact" | ...
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str  # provenance source string


@runtime_checkable
class AuditLog(Protocol):
    """Append-only audit log."""

    def append(self, entry: AuditEntry) -> None: ...

    def list_entries(self, limit: int = 100) -> list[AuditEntry]: ...
