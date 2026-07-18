"""Hybrid logical clock port and sortable timestamp value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class HlcTimestamp:
    """One persisted hybrid logical timestamp."""

    physical_ms: int
    logical_counter: int

    def comparison_key(self, device_id: str, op_id: str) -> tuple[int, int, str, str]:
        """Return the deterministic M5 ordering key."""
        return (self.physical_ms, self.logical_counter, device_id, op_id)


@runtime_checkable
class HybridLogicalClock(Protocol):
    """Emit monotonic local timestamps and absorb a remote timestamp."""

    @property
    def device_id(self) -> str: ...

    def tick(self) -> HlcTimestamp: ...

    def observe(self, remote: HlcTimestamp) -> HlcTimestamp: ...
