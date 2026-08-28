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


@dataclass(frozen=True)
class ExtractionIdentity:
    """The extraction-configuration identity of one prospective import.

    ``fingerprint`` distinguishes the same bytes parsed under materially different configuration,
    and nothing more. It is derived where the extractors live, because only they know which
    self-identity inputs reach a given source and how that source normalizes them: changing the
    self sender changes what a WhatsApp export extracts, while a LinkedIn CSV parses identically
    either way and must stay deduplicable across an unrelated change to the user's own aliases.

    ``contract_revision`` is People Context's own per-source extraction-contract identifier and
    participates in the fingerprint. Advancing one is how an intentional change in parsing
    semantics opts into a new claim identity instead of silently reusing a batch that was
    extracted under the old rules.

    The raw self identities the fingerprint was derived from are deliberately not carried here:
    a fingerprint is an idempotency key, and the values behind it are not stored merely to
    produce one.
    """

    contract_revision: str
    fingerprint: str


@dataclass(frozen=True)
class StableExtraction:
    """Candidates extracted from one verified stable snapshot of a source artifact.

    ``content_digest`` is the SHA-256 of exactly the bytes the candidates were parsed from, so a
    receipt built from it can never attach a digest of one file version to candidates of another.
    """

    content_digest: str
    extracted: ExtractedImport


@runtime_checkable
class ImportExtractor(Protocol):
    """Extract narrow candidates from one supported source without retaining raw content.

    ``self_names`` and ``self_sender`` are explicit optional self-resolution inputs for sources
    that identify participants by display label rather than by address. Extractors that cannot
    use them accept and ignore them; no source takes untyped keyword arguments.

    ``content_bytes`` is one immutable in-memory snapshot of the source. It exists so a caller
    that has already hashed a source can have it parsed from those exact bytes; the extractor
    decodes them with the same encoding rules it would have applied to the path, so nothing
    about the resulting candidates depends on which of the two the caller supplied. Exactly one
    of ``content``, ``content_bytes``, and ``path`` is accepted.

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
        content_bytes: bytes | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
    ) -> ExtractedImport: ...


@runtime_checkable
class StableSourceExtractor(Protocol):
    """Extract one source under a verified stable-snapshot guarantee.

    Implementations must ensure the returned digest and the returned candidates describe the same
    source snapshot. A path is not a snapshot, so an implementation either reads one bounded
    immutable byte snapshot and parses that, or verifies that a path-oriented parse ran against an
    unchanged file and discards the pass otherwise. Neither route may persist a raw copy of the
    user's source.
    """

    def extraction_identity(
        self,
        source_type: str,
        *,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
    ) -> ExtractionIdentity: ...

    def extract_stable(
        self,
        source_type: str,
        *,
        path: str,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
    ) -> StableExtraction: ...


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
