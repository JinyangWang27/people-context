"""Replayable local changelog port."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id


class ChangelogEntry(BaseModel):
    """One durable, idempotent operation ordered by an HLC."""

    op_id: str = Field(default_factory=new_id)
    device_id: str
    hlc_physical_ms: int
    hlc_logical: int
    transaction_id: str
    entity_type: str
    entity_id: str
    op_kind: str
    payload: dict[str, Any]
    changed_fields: list[str] = Field(default_factory=list)
    actor: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1
    inserted_at: datetime

    def comparison_key(self) -> tuple[int, int, str, str]:
        """Return the deterministic replication ordering key."""
        return (self.hlc_physical_ms, self.hlc_logical, self.device_id, self.op_id)


@runtime_checkable
class Changelog(Protocol):
    """Append and inspect replayable local operations."""

    def append(self, entry: ChangelogEntry) -> None: ...

    def list_entries(self, limit: int = 100, entity_id: str | None = None) -> list[ChangelogEntry]: ...
