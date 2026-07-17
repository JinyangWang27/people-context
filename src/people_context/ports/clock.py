"""Clock port: injectable source of the current time."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current timezone-aware UTC time."""

    def now(self) -> datetime: ...


class SystemClock:
    """Concrete clock backed by the system wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
