"""Replayable local changelog port."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id

#: The deterministic replication ordering key of one entry, as returned by
#: :meth:`ChangelogEntry.comparison_key`.
ChangelogCursor = tuple[int, int, str, str]


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

    def comparison_key(self) -> ChangelogCursor:
        """Return the deterministic replication ordering key."""
        return (self.hlc_physical_ms, self.hlc_logical, self.device_id, self.op_id)


@runtime_checkable
class Changelog(Protocol):
    """Append and inspect replayable local operations."""

    def append(self, entry: ChangelogEntry) -> None: ...

    def list_entries(self, limit: int | None = 100, entity_id: str | None = None) -> list[ChangelogEntry]:
        """Return entries newest first; ``limit=None`` returns every matching row."""
        ...

    def list_entries_after(self, cursor: ChangelogCursor | None, limit: int = 100) -> list[ChangelogEntry]:
        """Return up to `limit` entries ordered oldest first, strictly after `cursor`.

        The cursor is a full comparison key, so an entry is returned only when its own
        key sorts strictly after it; `None` starts before the minimum key and therefore
        replays everything. Comparing the complete key rather than the HLC alone keeps
        the tail exact when two devices mint the same physical/logical pair.
        """
        ...
