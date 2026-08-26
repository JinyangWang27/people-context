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


@dataclass(frozen=True)
class StagedBatchSize:
    """How large one staged batch is, measured without loading its candidate JSON.

    ``truncated`` says the measurement stopped at the caller's scan limit, so the batch has
    at least that many rows and ``payload_bytes`` covers only the rows that were scanned.
    """

    row_count: int
    payload_bytes: int
    truncated: bool


@runtime_checkable
class ImportExtractor(Protocol):
    """Extract narrow candidates from one supported source without retaining raw content.

    ``self_names`` and ``self_sender`` are explicit optional self-resolution inputs for sources
    that identify participants by display label rather than by address. Extractors that cannot
    use them accept and ignore them; no source takes untyped keyword arguments.

    ``max_source_bytes`` is the caller's read budget for a path-based source and
    ``max_candidates`` the ceiling on what that source may expand into. ``None`` keeps the
    released unbounded behavior, so only a boundary that chose a budget is bounded by one.
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
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
    ) -> ExtractedImport: ...


@runtime_checkable
class ImportStagingStore(Protocol):
    """Atomically stage and status import candidates."""

    def stage_batch(self, rows: list[StagedImportRow]) -> None: ...

    def list_batch(self, batch_id: str) -> list[StagedImportRow]: ...

    def mark_committed(self, candidate_ids: list[str]) -> None: ...


@runtime_checkable
class ImportStagingSizeReader(Protocol):
    """Measure one staged batch cheaply enough to decide whether it can be materialized.

    This is deliberately separate from `ImportStagingStore`: a caller that only needs to know
    whether a batch fits its budget must not gain the ability to read or commit it, and the
    measurement must not load a single candidate body to answer.
    """

    def measure_batch(self, batch_id: str, *, row_scan_limit: int) -> StagedBatchSize: ...
