"""Narrow ports for durable import source receipts and candidate commit outcomes.

A *source session* records that material was processed, never the material itself: a bounded
machine category, an optional caller-authored label, the SHA-256 digest of the exact stable
bytes an extraction ran over, and a fingerprint of the extraction-affecting configuration.

A *candidate mapping* records what one committed staging candidate durably produced. It is the
canonical record-to-source association as well as the commit-outcome record, so provenance and
retry resolution read one relation rather than two parallel ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

from people_context.domain.import_provenance import (
    CLAIM_KEY_SEPARATOR,
    EXTRACTION_FINGERPRINT_ABSENT,
    compose_claim_key,
)
from people_context.ports.imports import StagedImportRow

__all__ = [
    "CLAIM_KEY_SEPARATOR",
    "DISPOSITION_ENTITY",
    "DISPOSITION_MERGED_AWAY",
    "EXTRACTION_FINGERPRINT_ABSENT",
    "SOURCE_SESSION_STATUSES",
    "STATUS_COMMITTED",
    "STATUS_PARTIALLY_COMMITTED",
    "STATUS_REDACTED",
    "STATUS_STAGED",
    "CandidateMappingRow",
    "ImportSourceInspectionReader",
    "ImportSourceStore",
    "SourceCandidateTotals",
    "SourceClaimOutcome",
    "SourceSessionClaim",
    "SourceSessionRow",
    "compose_claim_key",
]

#: One staged batch is reviewable, none of it is committed yet.
STATUS_STAGED: Final = "staged"

#: Some candidates committed and some reviewable rows remain.
STATUS_PARTIALLY_COMMITTED: Final = "partially_committed"

#: Every accepted candidate committed; staging rows may since have been cleaned up.
STATUS_COMMITTED: Final = "committed"

#: Terminal: hard forget left no live mapping and no reviewable staging behind.
STATUS_REDACTED: Final = "redacted"

SOURCE_SESSION_STATUSES: Final[tuple[str, ...]] = (
    STATUS_STAGED,
    STATUS_PARTIALLY_COMMITTED,
    STATUS_COMMITTED,
    STATUS_REDACTED,
)

#: A committed candidate that produced or reused a durable entity.
DISPOSITION_ENTITY: Final = "entity"

#: A committed relationship candidate whose edge a later person merge removed as a self-loop.
DISPOSITION_MERGED_AWAY: Final = "merged_away"


@dataclass(frozen=True)
class SourceSessionClaim:
    """The bounded receipt metadata one staging attempt asserts about its source.

    ``forced`` marks an explicit reprocessing attempt. It keeps the same digest and fingerprint
    but asserts no canonical claim, so it never weakens the default uniqueness rule.
    """

    source_kind: str
    content_digest: str | None = None
    extraction_fingerprint: str | None = None
    extraction_contract_revision: str | None = None
    label: str | None = None
    external_source_id: str | None = None
    forced: bool = False

    @property
    def claim_key(self) -> str | None:
        """Return the canonical claim key this attempt competes for, if any."""
        if self.forced:
            return None
        return compose_claim_key(self.source_kind, self.content_digest, self.extraction_fingerprint)


@dataclass(frozen=True)
class SourceSessionRow:
    """One persisted source receipt."""

    id: str
    source_kind: str
    label: str | None
    external_source_id: str | None
    content_digest: str | None
    extraction_fingerprint: str | None
    extraction_contract_revision: str | None
    claim_key: str | None
    batch_id: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class CandidateMappingRow:
    """One committed candidate's durable outcome."""

    candidate_id: str
    batch_id: str
    source_session_id: str
    disposition: str
    entity_type: str
    entity_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class SourceClaimOutcome:
    """What one atomic claim-plus-stage attempt observed.

    ``created`` is ``False`` when a canonical claim was already owned. The loser of that race
    writes no durable row at all and reports the winner's session, so two concurrent processes
    can never both publish a default batch for one claim.

    ``candidate_count`` counts what the session's batch holds: its staged rows while they exist,
    and otherwise its durable commit mappings. A fully committed batch may have had its staging
    cleaned up, or arrived from a bundle that carries mappings but no reviewable rows, and
    reporting nought candidates for it would misdescribe an import that produced records.

    ``reviewable`` says whether staged rows remain, so a caller can tell "already imported, still
    awaiting review" from "already imported and committed" instead of pointing someone at a batch
    that review can no longer find.
    """

    session: SourceSessionRow
    created: bool
    candidate_count: int
    reviewable: bool = True


@runtime_checkable
class ImportSourceStore(Protocol):
    """Persist source receipts and candidate commit outcomes atomically."""

    def claim_and_stage(
        self,
        claim: SourceSessionClaim,
        rows: list[StagedImportRow],
        *,
        session_id: str,
        batch_id: str,
        created_at: datetime,
    ) -> SourceClaimOutcome:
        """Publish claim, receipt, and every staged candidate row in one transaction."""
        ...

    def session_for_batch(self, batch_id: str) -> SourceSessionRow | None: ...

    def set_session_status(self, session_id: str, status: str) -> None: ...

    def record_mappings(self, mappings: list[CandidateMappingRow]) -> None: ...

    def mappings_for_batch(self, batch_id: str) -> list[CandidateMappingRow]: ...


@dataclass(frozen=True)
class SourceCandidateTotals:
    """SQL aggregates describing one source's candidates without reading any of them.

    Both halves are needed because they answer different questions and neither subsumes the
    other: staging rows say what is left to review and disappear under cleanup policy, while
    mappings say what the source durably produced and outlive those rows entirely.

    The pairs are ordered by key so a rendered summary is deterministic.
    """

    mappings_total: int
    mappings_by_disposition: tuple[tuple[str, int], ...] = ()
    staged_total: int = 0
    staged_by_status: tuple[tuple[str, int], ...] = ()


@runtime_checkable
class ImportSourceInspectionReader(Protocol):
    """Read source receipts and their commit outcomes in bounded keyset pages.

    Every listing method takes the page size the application validated and returns at most
    ``limit + 1`` rows, so one query both fills a page and settles whether another exists. The
    extra row is the reader's whole contribution to that decision: truncation, cursors, and what
    a redacted receipt may disclose are application policy.

    A source listing resumes from an identifier rather than from a sort key, because a caller
    holds the cursor and a cursor is reversible. Resolving that identifier to its own position is
    the store's job, so the key never has to leave the database — which matters for a terminal
    redacted receipt, whose timestamps inspection must not disclose.
    """

    def sort_key_for_session(self, session_id: str) -> tuple[datetime, str] | None:
        """Return one receipt's `(created_at, id)` listing position, or None if it is gone."""
        ...

    def list_sessions(
        self,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> list[SourceSessionRow]:
        """Return one newest-first page after `after`, ordered by `(created_at DESC, id DESC)`."""
        ...

    def get_session(self, session_id: str) -> SourceSessionRow | None: ...

    def count_source_candidates(self, session_id: str, batch_id: str | None) -> SourceCandidateTotals:
        """Return aggregate candidate totals for one source, computed in the store."""
        ...

    def list_session_mappings(
        self,
        session_id: str,
        *,
        limit: int,
        after: str | None = None,
    ) -> list[CandidateMappingRow]:
        """Return one page of the source's mappings after `after`, ordered by `candidate_id ASC`."""
        ...
