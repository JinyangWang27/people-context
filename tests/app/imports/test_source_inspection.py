"""Source inspection policy: bounded pages, deterministic cursors, and redaction.

These exercise the use cases against a fake reader, so what is under test is the application's
own decisions — page validation, where a cursor resumes, and what a terminal receipt may say —
rather than any SQL. The SQLite counterpart lives in `tests/adapters/sqlite/`.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from people_context.app.imports import (
    DEFAULT_SOURCE_PAGE_LIMIT,
    INVALID_SOURCE_CURSOR,
    INVALID_SOURCE_PAGE_LIMIT,
    MAX_SOURCE_PAGE_LIMIT,
    UNKNOWN_SOURCE_SESSION,
    ListImportSources,
    ShowImportSource,
    SourceInspectionError,
)
from people_context.domain.source_cursor import SOURCE_LIST_SCOPE, encode_cursor
from people_context.ports.sources import (
    DISPOSITION_ENTITY,
    DISPOSITION_MERGED_AWAY,
    STATUS_COMMITTED,
    STATUS_REDACTED,
    CandidateMappingRow,
    SourceCandidateTotals,
    SourceSessionRow,
)

_START = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


class FakeSourceInspectionReader:
    """An in-memory reader that honours the port's `limit + 1` contract.

    It records every page request, which is how these tests assert that a page really was asked
    for as a page — a reader that returned everything would make the use case look bounded while
    the read behind it was not.
    """

    def __init__(
        self,
        sessions: list[SourceSessionRow] | None = None,
        mappings: list[CandidateMappingRow] | None = None,
        totals: SourceCandidateTotals | None = None,
    ) -> None:
        self.sessions = sessions or []
        self.mappings = mappings or []
        self.totals = totals or SourceCandidateTotals(mappings_total=0)
        self.session_requests: list[tuple[int, tuple[datetime, str] | None]] = []
        self.key_requests: list[str] = []
        self.mapping_requests: list[tuple[str, int, str | None]] = []
        self.count_requests: list[tuple[str, str | None]] = []

    def sort_key_for_session(self, session_id: str) -> tuple[datetime, str] | None:
        self.key_requests.append(session_id)
        row = self.get_session(session_id)
        return (row.created_at, row.id) if row is not None else None

    def list_sessions(
        self,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> list[SourceSessionRow]:
        self.session_requests.append((limit, after))
        ordered = sorted(self.sessions, key=lambda row: (row.created_at, row.id), reverse=True)
        if after is not None:
            ordered = [row for row in ordered if (row.created_at, row.id) < after]
        return ordered[: limit + 1]

    def get_session(self, session_id: str) -> SourceSessionRow | None:
        return next((row for row in self.sessions if row.id == session_id), None)

    def count_source_candidates(self, session_id: str, batch_id: str | None) -> SourceCandidateTotals:
        self.count_requests.append((session_id, batch_id))
        return self.totals

    def list_session_mappings(
        self,
        session_id: str,
        *,
        limit: int,
        after: str | None = None,
    ) -> list[CandidateMappingRow]:
        self.mapping_requests.append((session_id, limit, after))
        ordered = sorted(
            (row for row in self.mappings if row.source_session_id == session_id),
            key=lambda row: row.candidate_id,
        )
        if after is not None:
            ordered = [row for row in ordered if row.candidate_id > after]
        return ordered[: limit + 1]


def _session(
    index: int,
    *,
    status: str = STATUS_COMMITTED,
    claim_key: str | None = "linkedin\x1fdigest\x1ffingerprint-absent",
    label: str | None = "Work connections",
    fingerprint: str | None = None,
) -> SourceSessionRow:
    return SourceSessionRow(
        id=f"S{index:04d}",
        source_kind="linkedin",
        label=label,
        external_source_id="crm-42",
        content_digest="a" * 64,
        extraction_fingerprint=fingerprint,
        extraction_contract_revision="linkedin.1",
        claim_key=claim_key,
        batch_id=f"B{index:04d}",
        status=status,
        created_at=_START + timedelta(minutes=index),
    )


def _mapping(index: int, *, session_id: str = "S0001", disposition: str = DISPOSITION_ENTITY) -> CandidateMappingRow:
    return CandidateMappingRow(
        candidate_id=f"C{index:04d}",
        batch_id="B0001",
        source_session_id=session_id,
        disposition=disposition,
        entity_type="person" if disposition == DISPOSITION_ENTITY else "relationship",
        entity_id=f"P{index:04d}" if disposition == DISPOSITION_ENTITY else None,
        created_at=_START,
    )


def test_sources_are_listed_newest_first() -> None:
    reader = FakeSourceInspectionReader([_session(1), _session(3), _session(2)])

    result = ListImportSources(reader).execute()

    assert [row.id for row in result.sources] == ["S0003", "S0002", "S0001"]
    assert result.limit == DEFAULT_SOURCE_PAGE_LIMIT
    assert result.next_cursor is None


def test_a_full_listing_page_reports_where_to_resume() -> None:
    reader = FakeSourceInspectionReader([_session(index) for index in range(1, 6)])

    first = ListImportSources(reader).execute(limit=2)

    assert [row.id for row in first.sources] == ["S0005", "S0004"]
    assert first.next_cursor is not None
    # The reader was asked for a page, not for the table.
    assert reader.session_requests == [(2, None)]


def test_resuming_a_listing_returns_the_next_page_without_skipping_a_row() -> None:
    """A cursor names the last row returned, so the row after it opens the next page."""
    reader = FakeSourceInspectionReader([_session(index) for index in range(1, 6)])
    use_case = ListImportSources(reader)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = use_case.execute(limit=2, cursor=cursor)
        seen.extend(row.id for row in page.sources)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == ["S0005", "S0004", "S0003", "S0002", "S0001"]


def test_a_listing_page_that_exactly_fills_the_limit_ends_the_traversal() -> None:
    reader = FakeSourceInspectionReader([_session(1), _session(2)])

    result = ListImportSources(reader).execute(limit=2)

    assert [row.id for row in result.sources] == ["S0002", "S0001"]
    assert result.next_cursor is None


@pytest.mark.parametrize("limit", [0, -1, MAX_SOURCE_PAGE_LIMIT + 1])
def test_a_page_limit_outside_its_range_is_refused(limit: int) -> None:
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError) as raised:
        ListImportSources(reader).execute(limit=limit)

    assert raised.value.code == INVALID_SOURCE_PAGE_LIMIT
    assert reader.session_requests == []


def test_an_unrecognized_cursor_is_refused_before_any_read() -> None:
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError) as raised:
        ListImportSources(reader).execute(cursor="not-a-cursor-this-surface-issued")

    assert raised.value.code == INVALID_SOURCE_CURSOR
    assert reader.session_requests == []


def test_showing_a_source_pages_its_mappings_and_reports_sql_counts() -> None:
    reader = FakeSourceInspectionReader(
        [_session(1)],
        [_mapping(index) for index in range(1, 6)],
        SourceCandidateTotals(
            mappings_total=5,
            mappings_by_disposition=(("entity", 5),),
            staged_total=6,
            staged_by_status=(("committed", 5), ("pending", 1)),
        ),
    )

    result = ShowImportSource(reader).execute("S0001", limit=2)

    assert [row.candidate_id for row in result.mappings] == ["C0001", "C0002"]
    assert result.next_cursor is not None
    # Counts describe the whole source, not the page in front of them.
    assert result.counts.mappings_total == 5
    assert result.counts.staged_by_status == {"committed": 5, "pending": 1}
    assert reader.mapping_requests == [("S0001", 2, None)]


def test_resuming_a_mapping_page_traverses_the_source_without_one_unbounded_response() -> None:
    reader = FakeSourceInspectionReader([_session(1)], [_mapping(index) for index in range(1, 6)])
    use_case = ShowImportSource(reader)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = use_case.execute("S0001", limit=2, cursor=cursor)
        seen.extend(row.candidate_id for row in page.mappings)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == ["C0001", "C0002", "C0003", "C0004", "C0005"]
    assert [request[1] for request in reader.mapping_requests] == [2, 2, 2]


def test_a_merged_away_outcome_names_no_removed_edge() -> None:
    reader = FakeSourceInspectionReader(
        [_session(1)],
        [_mapping(1, disposition=DISPOSITION_MERGED_AWAY)],
    )

    result = ShowImportSource(reader).execute("S0001")

    assert result.mappings[0].disposition == DISPOSITION_MERGED_AWAY
    assert result.mappings[0].entity_type == "relationship"
    assert result.mappings[0].entity_id is None


def test_an_unknown_source_session_is_refused() -> None:
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError) as raised:
        ShowImportSource(reader).execute("S9999")

    assert raised.value.code == UNKNOWN_SOURCE_SESSION


@pytest.mark.parametrize("session_id", ["", "   "])
def test_a_blank_session_id_is_refused_before_any_query(session_id: str) -> None:
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError) as raised:
        ShowImportSource(reader).execute(session_id)

    assert raised.value.code == UNKNOWN_SOURCE_SESSION


def test_a_session_id_reaches_the_reader_exactly_as_given() -> None:
    """Restore preserves ids verbatim, so normalizing one here could unmatch the row it names."""
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError):
        ShowImportSource(reader).execute("  S0001  ")


def test_a_session_id_a_bootstrap_restore_accepts_is_inspectable() -> None:
    """A restored id may be neither a ULID nor ASCII; its provenance must still be readable."""
    restored = replace(_session(1), id="urn:uuid:9f8a/7b+6c источник " + "A" * 1000)
    reader = FakeSourceInspectionReader([restored])

    assert ShowImportSource(reader).execute(restored.id).source.id == restored.id


def test_a_listing_cursor_carries_only_the_identifier_of_its_last_row() -> None:
    """A redacted receipt can end a page, and its timestamp must not travel in the cursor."""
    redacted = replace(_session(1), status=STATUS_REDACTED)
    reader = FakeSourceInspectionReader([redacted, _session(2)])

    page = ListImportSources(reader).execute(limit=1, cursor=encode_cursor(SOURCE_LIST_SCOPE, "S0002"))

    assert [row.id for row in page.sources] == ["S0001"]
    assert page.next_cursor is None
    # The store resolved the position; the caller never held it.
    assert reader.key_requests == ["S0002"]


def test_a_redacted_row_ending_a_page_leaks_no_timestamp_through_the_cursor() -> None:
    reader = FakeSourceInspectionReader(
        [replace(_session(index), status=STATUS_REDACTED) for index in range(1, 4)]
    )

    page = ListImportSources(reader).execute(limit=1)

    assert page.next_cursor is not None
    decoded = base64.urlsafe_b64decode(page.next_cursor + "=" * (-len(page.next_cursor) % 4)).decode("utf-8")
    assert decoded.endswith("S0003")
    assert "2026" not in decoded


def test_another_sources_mapping_cursor_is_refused_rather_than_silently_skipping() -> None:
    """The dangerous shape: a bare boundary would page A while omitting what sorts below B's key."""
    reader = FakeSourceInspectionReader(
        [_session(1), _session(2)],
        [_mapping(index, session_id="S0001") for index in range(1, 6)]
        + [_mapping(index, session_id="S0002") for index in range(1, 6)],
    )
    from_other_source = ShowImportSource(reader).execute("S0002", limit=2).next_cursor
    assert from_other_source is not None

    with pytest.raises(SourceInspectionError) as raised:
        ShowImportSource(reader).execute("S0001", cursor=from_other_source)

    assert raised.value.code == INVALID_SOURCE_CURSOR


def test_a_listing_cursor_is_refused_by_a_mapping_page_and_the_reverse() -> None:
    reader = FakeSourceInspectionReader(
        [_session(1), _session(2)],
        [_mapping(index) for index in range(1, 6)],
    )
    listing = ListImportSources(reader).execute(limit=1).next_cursor
    mappings = ShowImportSource(reader).execute("S0001", limit=2).next_cursor
    assert listing is not None and mappings is not None

    with pytest.raises(SourceInspectionError):
        ShowImportSource(reader).execute("S0001", cursor=listing)
    with pytest.raises(SourceInspectionError):
        ListImportSources(reader).execute(cursor=mappings)


def test_a_mapping_cursor_is_accepted_by_the_source_that_issued_it() -> None:
    reader = FakeSourceInspectionReader([_session(1)], [_mapping(index) for index in range(1, 6)])
    use_case = ShowImportSource(reader)
    own = use_case.execute("S0001", limit=2).next_cursor
    assert own is not None

    assert [row.candidate_id for row in use_case.execute("S0001", limit=2, cursor=own).mappings] == [
        "C0003",
        "C0004",
    ]


def test_a_cursor_naming_a_source_that_is_gone_is_refused() -> None:
    """A source hard-forgotten between two pages must not silently restart the listing."""
    reader = FakeSourceInspectionReader([_session(1)])

    with pytest.raises(SourceInspectionError) as raised:
        ListImportSources(reader).execute(cursor=encode_cursor(SOURCE_LIST_SCOPE, "S9999"))

    assert raised.value.code == INVALID_SOURCE_CURSOR
    assert reader.session_requests == []


def test_a_redacted_source_discloses_only_its_id_kind_and_claim() -> None:
    redacted = SourceSessionRow(
        id="S0001",
        source_kind="linkedin",
        label=None,
        external_source_id=None,
        content_digest="b" * 64,
        extraction_fingerprint=None,
        extraction_contract_revision=None,
        claim_key="linkedin\x1f" + "b" * 64 + "\x1ffingerprint-absent",
        batch_id=None,
        status=STATUS_REDACTED,
        # The column survives redaction in storage; inspection must not report it.
        created_at=_START,
    )
    reader = FakeSourceInspectionReader([redacted], [_mapping(1)])

    result = ShowImportSource(reader).execute("S0001")

    assert result.source.redacted is True
    assert result.source.claimed is True
    assert result.source.content_digest == "b" * 64
    assert result.source.created_at is None
    assert result.source.batch_id is None
    assert result.source.label is None
    assert result.source.external_source_id is None
    assert result.source.extraction_contract_revision is None
    assert result.mappings == []
    assert result.counts.mappings_total == 0
    # Neither the mapping page nor the counts were even asked for.
    assert reader.mapping_requests == []
    assert reader.count_requests == []


def test_pagination_arguments_do_not_widen_a_redacted_source() -> None:
    redacted = SourceSessionRow(
        id="S0001",
        source_kind="linkedin",
        label=None,
        external_source_id=None,
        content_digest="b" * 64,
        extraction_fingerprint=None,
        extraction_contract_revision=None,
        claim_key="claim",
        batch_id=None,
        status=STATUS_REDACTED,
        created_at=_START,
    )
    reader = FakeSourceInspectionReader([redacted], [_mapping(index) for index in range(1, 6)])
    cursor = ShowImportSource(FakeSourceInspectionReader([_session(1)], [_mapping(1)])).execute("S0001")

    result = ShowImportSource(reader).execute(
        "S0001",
        limit=MAX_SOURCE_PAGE_LIMIT,
        cursor=cursor.next_cursor,
    )

    assert result.mappings == []
    assert result.next_cursor is None


def test_a_redacted_source_still_refuses_a_malformed_cursor() -> None:
    """Refusing only when a source has mappings would make the refusal itself a disclosure."""
    redacted = SourceSessionRow(
        id="S0001",
        source_kind="linkedin",
        label=None,
        external_source_id=None,
        content_digest="b" * 64,
        extraction_fingerprint=None,
        extraction_contract_revision=None,
        claim_key="claim",
        batch_id=None,
        status=STATUS_REDACTED,
        created_at=_START,
    )

    with pytest.raises(SourceInspectionError) as raised:
        ShowImportSource(FakeSourceInspectionReader([redacted])).execute("S0001", cursor="!!!")

    assert raised.value.code == INVALID_SOURCE_CURSOR


def test_a_source_with_no_canonical_claim_reports_that_it_makes_no_promise() -> None:
    reader = FakeSourceInspectionReader([_session(1, claim_key=None)])

    result = ListImportSources(reader).execute()

    assert result.sources[0].claimed is False


def test_a_claim_backed_source_without_a_fingerprint_is_an_explicit_absence() -> None:
    reader = FakeSourceInspectionReader([_session(1, fingerprint=None)])

    result = ListImportSources(reader).execute()

    assert result.sources[0].claimed is True
    assert result.sources[0].extraction_fingerprint is None


def test_an_empty_store_lists_nothing_rather_than_failing() -> None:
    result = ListImportSources(FakeSourceInspectionReader()).execute()

    assert result.sources == []
    assert result.next_cursor is None
