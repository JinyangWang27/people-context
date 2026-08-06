"""Read-side port for stored aggregate signals used by daily-utility reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RecencySignal:
    """Stored recency aggregate for one active person.

    The adapter reports stored signal only: relationship-to-self categories that are
    active on the requested date, plus the latest timestamp and count of that person's
    ordinary-disclosure interactions. Age, threshold, ordering, and caps are application
    policy and are never computed here.
    """

    person_id: str
    name: str
    categories: tuple[str, ...] = field(default_factory=tuple)
    last_interaction_at: datetime | None = None
    interaction_count: int = 0


@runtime_checkable
class RecencyReader(Protocol):
    """Read per-person interaction recency without disclosing interaction content."""

    def list_recency_signals(self, *, as_of: date, category: str | None = None) -> list[RecencySignal]: ...
