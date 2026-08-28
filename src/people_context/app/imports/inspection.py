"""Bounded source inspection: "where did this record come from?", and nothing wider.

Inspection reads the M18.1 relations that already exist — the source receipt and the candidate
commit mappings — rather than a second provenance table. That is deliberate: a mapping already
names the source session that produced a durable entity, so a parallel record-to-source table
would be a second truth to keep in step with merge, forget, and restore.

Two rules shape everything here.

**Every read is a page.** A database that has imported a mailbox holds as many mappings as the
mailbox held messages, so listing is keyset-paginated at the store and the application only ever
holds one page. Nothing here fetches a table and slices it.

**A redacted receipt discloses almost nothing.** Hard forget reduces a fully erased source to the
minimum that keeps its claim non-restageable, and inspection must not undo that by reporting the
timestamps, batch, counts, or mappings its row still structurally has or once had. So redaction is
checked before anything else is read: a terminal receipt returns its id, its non-personal kind, and
its claim state, and the mapping page is never queried at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, TypeVar

from pydantic import BaseModel, Field

from people_context.domain.source_cursor import (
    SOURCE_LIST_SCOPE,
    decode_cursor,
    encode_cursor,
    mapping_scope,
)
from people_context.ports.sources import (
    STATUS_REDACTED,
    CandidateMappingRow,
    ImportSourceInspectionReader,
    SourceCandidateTotals,
    SourceSessionRow,
)

#: Sources or mappings one page may carry when the caller says nothing.
DEFAULT_SOURCE_PAGE_LIMIT: Final = 50

#: The narrowest and widest page a caller may ask for. The ceiling is the point: it bounds one
#: response, and repeated calls are how a large source is traversed.
MIN_SOURCE_PAGE_LIMIT: Final = 1
MAX_SOURCE_PAGE_LIMIT: Final = 200

#: Stable refusal for a source session id that names nothing.
UNKNOWN_SOURCE_SESSION: Final = "unknown_source_session"

#: Stable refusal for a pagination cursor this surface did not issue.
INVALID_SOURCE_CURSOR: Final = "invalid_source_cursor"

#: Stable refusal for a page size outside its documented range.
INVALID_SOURCE_PAGE_LIMIT: Final = "invalid_source_page_limit"

_RowT = TypeVar("_RowT")


class SourceInspectionError(ValueError):
    """Raised when an inspection parameter is outside its documented bounds.

    ``code`` is stable so a caller can distinguish "no such source" from "bad page argument"
    without matching on wording.
    """

    def __init__(self, message: str, *, code: str = INVALID_SOURCE_PAGE_LIMIT) -> None:
        super().__init__(message)
        self.code = code


class SourceSummary(BaseModel):
    """One source receipt as inspection may disclose it.

    A terminal ``redacted`` receipt carries only ``id``, ``source_kind``, ``status``, and its
    claim state; every optional field is ``None`` because hard forget cleared it or because
    reporting it would restore something erasure removed. ``created_at`` is the second kind:
    the column survives redaction, so it is withheld here rather than there.

    ``claimed`` says whether this receipt owns a canonical duplicate claim. Together with
    ``extraction_fingerprint`` it is the claim state a redacted receipt keeps: ``claimed`` with no
    fingerprint is the explicit absence state, which is a different thing from a receipt that
    competes for no claim at all.
    """

    id: str
    source_kind: str
    status: str
    redacted: bool = False
    claimed: bool = False
    content_digest: str | None = None
    extraction_fingerprint: str | None = None
    extraction_contract_revision: str | None = None
    label: str | None = None
    external_source_id: str | None = None
    batch_id: str | None = None
    created_at: datetime | None = None


class SourceMappingEntry(BaseModel):
    """One committed candidate's durable outcome.

    A ``merged_away`` entry carries no ``entity_id``: the relationship it produced was removed as
    a self-loop during a person merge, and the terminal outcome deliberately does not name the
    edge that no longer exists.
    """

    candidate_id: str
    disposition: str
    entity_type: str
    entity_id: str | None = None


class SourceCandidateCounts(BaseModel):
    """Aggregate candidate totals for one source, computed in SQL."""

    mappings_total: int = 0
    mappings_by_disposition: dict[str, int] = Field(default_factory=dict)
    staged_total: int = 0
    staged_by_status: dict[str, int] = Field(default_factory=dict)


class SourceListResult(BaseModel):
    """One bounded newest-first page of source receipts."""

    limit: int
    sources: list[SourceSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class SourceDetailResult(BaseModel):
    """One source receipt with a bounded page of its candidate outcomes.

    ``counts`` and ``mappings`` are empty for a redacted source, which is the absence of
    disclosure rather than a source that produced nothing.
    """

    source: SourceSummary
    counts: SourceCandidateCounts = Field(default_factory=SourceCandidateCounts)
    limit: int
    mappings: list[SourceMappingEntry] = Field(default_factory=list)
    next_cursor: str | None = None


class ListImportSources:
    """Page the local source receipts, newest first."""

    def __init__(self, reader: ImportSourceInspectionReader) -> None:
        self._reader = reader

    def execute(
        self,
        *,
        limit: int = DEFAULT_SOURCE_PAGE_LIMIT,
        cursor: str | None = None,
    ) -> SourceListResult:
        """Return at most `limit` receipts after `cursor`, and where the next page resumes.

        The cursor names a row, and the store resolves that row's sort position, so a redacted
        receipt's withheld timestamp never travels through the caller's hands. A cursor whose
        row is gone — a source hard-forgotten between two pages — is refused rather than silently
        restarting the listing at the top.
        """
        page_limit = _checked_limit(limit)
        after = None
        if cursor is not None:
            after = self._reader.sort_key_for_session(_decoded(cursor, SOURCE_LIST_SCOPE))
            if after is None:
                raise SourceInspectionError(
                    "this cursor no longer names a source", code=INVALID_SOURCE_CURSOR
                )
        rows = self._reader.list_sessions(limit=page_limit, after=after)
        page, next_row = _split(rows, page_limit)
        return SourceListResult(
            limit=page_limit,
            sources=[_summary(row) for row in page],
            # The cursor names the last row *returned*, never the peeked one, so resuming from it
            # yields the row after the page rather than skipping it.
            next_cursor=None if next_row is None else encode_cursor(SOURCE_LIST_SCOPE, page[-1].id),
        )


class ShowImportSource:
    """Show one source receipt and a bounded page of what its candidates produced."""

    def __init__(self, reader: ImportSourceInspectionReader) -> None:
        self._reader = reader

    def execute(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_SOURCE_PAGE_LIMIT,
        cursor: str | None = None,
    ) -> SourceDetailResult:
        """Return one receipt plus at most `limit` of its mappings after `cursor`.

        Page arguments are validated even for a redacted source. Refusing a malformed cursor only
        when the source happens to have mappings would make the refusal itself report on the
        source's state, and a caller paging a list has no way to know which entry that is.
        """
        page_limit = _checked_limit(limit)
        session = self._reader.get_session(_checked_session_id(session_id))
        if session is None:
            raise SourceInspectionError("no such source session", code=UNKNOWN_SOURCE_SESSION)
        # The cursor is scoped to this source, so it is decoded once the source is known and
        # still before the redaction return below: a terminal receipt refuses a bad cursor too.
        after = _decoded(cursor, mapping_scope(session.id)) if cursor is not None else None
        summary = _summary(session)
        if summary.redacted:
            # Nothing below this line runs for a terminal receipt: not the counts, not the mapping
            # page. Pagination arguments do not widen what redaction left.
            return SourceDetailResult(source=summary, limit=page_limit)
        rows = self._reader.list_session_mappings(session.id, limit=page_limit, after=after)
        page, next_row = _split(rows, page_limit)
        return SourceDetailResult(
            source=summary,
            counts=_counts(self._reader.count_source_candidates(session.id, session.batch_id)),
            limit=page_limit,
            mappings=[_mapping_entry(row) for row in page],
            next_cursor=(
                None if next_row is None else encode_cursor(mapping_scope(session.id), page[-1].candidate_id)
            ),
        )


def _checked_limit(limit: int) -> int:
    if limit < MIN_SOURCE_PAGE_LIMIT or limit > MAX_SOURCE_PAGE_LIMIT:
        raise SourceInspectionError(
            f"limit must be between {MIN_SOURCE_PAGE_LIMIT} and {MAX_SOURCE_PAGE_LIMIT}"
        )
    return limit


def _checked_session_id(session_id: str) -> str:
    """Refuse a blank id, and pass anything else through exactly as given.

    There is deliberately no length or alphabet rule. A bootstrap restore preserves identifiers
    verbatim and requires only that they are non-blank, so a narrower rule here would refuse a
    restored source that this database genuinely holds — and the id reaches SQLite only as a bound
    parameter of an exact lookup, so nothing is bought by narrowing it. Normalizing is out for the
    same reason: trimming could stop an id matching the very row it names.
    """
    if not session_id.strip():
        raise SourceInspectionError("no such source session", code=UNKNOWN_SOURCE_SESSION)
    return session_id


def _decoded(cursor: str, scope: str) -> str:
    """Decode one opaque cursor for the listing that must have issued it.

    Passing another listing's cursor is refused rather than treated as a boundary in this one,
    which would silently omit the rows sorting below it and report success.
    """
    try:
        return decode_cursor(cursor, scope=scope)
    except ValueError as exc:
        raise SourceInspectionError(str(exc), code=INVALID_SOURCE_CURSOR) from None


def _split(rows: list[_RowT], limit: int) -> tuple[list[_RowT], _RowT | None]:
    """Split a `limit + 1` read into the page and the row that proves another page exists."""
    return rows[:limit], rows[limit] if len(rows) > limit else None


def _summary(row: SourceSessionRow) -> SourceSummary:
    """Project one stored receipt into what inspection may disclose about it."""
    claimed = row.claim_key is not None
    if row.status == STATUS_REDACTED:
        return SourceSummary(
            id=row.id,
            source_kind=row.source_kind,
            status=row.status,
            redacted=True,
            claimed=claimed,
            content_digest=row.content_digest,
            extraction_fingerprint=row.extraction_fingerprint,
        )
    return SourceSummary(
        id=row.id,
        source_kind=row.source_kind,
        status=row.status,
        claimed=claimed,
        content_digest=row.content_digest,
        extraction_fingerprint=row.extraction_fingerprint,
        extraction_contract_revision=row.extraction_contract_revision,
        label=row.label,
        external_source_id=row.external_source_id,
        batch_id=row.batch_id,
        created_at=row.created_at,
    )


def _counts(totals: SourceCandidateTotals) -> SourceCandidateCounts:
    return SourceCandidateCounts(
        mappings_total=totals.mappings_total,
        mappings_by_disposition=dict(totals.mappings_by_disposition),
        staged_total=totals.staged_total,
        staged_by_status=dict(totals.staged_by_status),
    )


def _mapping_entry(row: CandidateMappingRow) -> SourceMappingEntry:
    return SourceMappingEntry(
        candidate_id=row.candidate_id,
        disposition=row.disposition,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
    )
