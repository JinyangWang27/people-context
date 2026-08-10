"""Plain DTOs and narrow ports for staged external imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ImportPersonCandidate:
    """Header-derived correspondent, deduplicated by email address."""

    name: str
    email: str
    alternate_names: list[str] = field(default_factory=list)
    message_id: str | None = None
    date: datetime | None = None


@dataclass(frozen=True)
class ImportInteractionCandidate:
    """One dated message represented without any body content."""

    participant_emails: list[str]
    occurred_at: datetime
    message_id: str | None = None


@dataclass(frozen=True)
class ExtractedImport:
    """All header-only candidates extracted from one input batch."""

    people: list[ImportPersonCandidate]
    interactions: list[ImportInteractionCandidate]
    skipped_message_ids: list[str] = field(default_factory=list)
    skipped_without_id: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    skipped_cards: list[dict[str, int | str]] = field(default_factory=list)


@dataclass(frozen=True)
class StagedImportRow:
    """One persisted candidate row."""

    id: str
    batch_id: str
    source: str
    candidate: dict[str, Any]
    status: str
    created_at: datetime


@runtime_checkable
class ImportExtractor(Protocol):
    """Extract narrow candidates from one supported source without retaining raw content.

    ``self_names`` and ``self_sender`` are explicit optional self-resolution inputs for sources
    that identify participants by display label rather than by address. Extractors that cannot
    use them accept and ignore them; no source takes untyped keyword arguments.
    """

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
    ) -> ExtractedImport: ...


@runtime_checkable
class ImportStagingStore(Protocol):
    """Atomically stage and status import candidates."""

    def stage_batch(self, rows: list[StagedImportRow]) -> None: ...

    def list_batch(self, batch_id: str) -> list[StagedImportRow]: ...

    def mark_committed(self, candidate_ids: list[str]) -> None: ...
