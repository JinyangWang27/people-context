"""The inspection reads are pages in SQL, not slices of a table the reader already loaded."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from people_context.adapters.sqlite import open_db
from people_context.adapters.sqlite.source_store import SqliteImportSourceStore
from people_context.ports.imports import StagedImportRow
from people_context.ports.sources import (
    DISPOSITION_ENTITY,
    DISPOSITION_MERGED_AWAY,
    CandidateMappingRow,
    SourceSessionClaim,
)

_START = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "inspection.db")
    yield connection
    connection.close()


def _store(conn: sqlite3.Connection) -> SqliteImportSourceStore:
    return SqliteImportSourceStore(conn)


def _stage(
    store: SqliteImportSourceStore,
    index: int,
    *,
    created_at: datetime | None = None,
    session_id: str | None = None,
    candidates: int = 0,
) -> str:
    """Publish one receipt through the production claim path and return its id."""
    identifier = session_id or f"S{index:04d}"
    batch_id = f"B{index:04d}"
    rows = [
        StagedImportRow(
            id=f"{batch_id}-R{position:04d}",
            batch_id=batch_id,
            source="import/linkedin",
            candidate={"type": "person", "canonical_name": f"Person {position}"},
            status="pending",
            created_at=_START,
        )
        for position in range(candidates)
    ]
    store.claim_and_stage(
        SourceSessionClaim(source_kind="linkedin", content_digest=f"{index:064d}"),
        rows,
        session_id=identifier,
        batch_id=batch_id,
        created_at=created_at or _START + timedelta(minutes=index),
    )
    return identifier


def _mappings(session_id: str, batch_id: str, count: int) -> list[CandidateMappingRow]:
    return [
        CandidateMappingRow(
            candidate_id=f"{batch_id}-C{position:04d}",
            batch_id=batch_id,
            source_session_id=session_id,
            disposition=DISPOSITION_ENTITY,
            entity_type="person",
            entity_id=f"{batch_id}-P{position:04d}",
            created_at=_START,
        )
        for position in range(count)
    ]


def test_sessions_are_returned_newest_first(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    for index in (1, 3, 2):
        _stage(store, index)

    rows = store.list_sessions(limit=10)

    assert [row.id for row in rows] == ["S0003", "S0002", "S0001"]


def test_sessions_sharing_a_timestamp_break_the_tie_by_descending_id(conn: sqlite3.Connection) -> None:
    """Without a stable tie-break, a cursor could skip or repeat a row at a timestamp boundary."""
    store = _store(conn)
    for index in (1, 2, 3):
        _stage(store, index, created_at=_START)

    rows = store.list_sessions(limit=10)

    assert [row.id for row in rows] == ["S0003", "S0002", "S0001"]


def test_a_listing_reads_exactly_one_row_past_the_page(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    for index in range(1, 51):
        _stage(store, index)

    rows = store.list_sessions(limit=2)

    assert len(rows) == 3


def test_the_last_page_of_a_listing_returns_no_extra_row(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1)
    _stage(store, 2)

    assert len(store.list_sessions(limit=2)) == 2


def test_a_listing_cursor_resumes_after_its_key_including_a_timestamp_tie(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    for index in (1, 2, 3):
        _stage(store, index, created_at=_START)

    first = store.list_sessions(limit=1)
    resumed = store.list_sessions(limit=10, after=(first[0].created_at, first[0].id))

    assert first[0].id == "S0003"
    assert [row.id for row in resumed] == ["S0002", "S0001"]


def test_the_listing_query_seeks_through_the_recent_index(conn: sqlite3.Connection) -> None:
    """The page must be an index seek; a scan would grow with the table it is paging."""
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM import_source_sessions "
        "WHERE created_at < ? OR (created_at = ? AND id < ?) "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        ("x", "x", "y", 3),
    ).fetchall()

    assert any("idx_import_source_sessions_recent" in str(row["detail"]) for row in plan)


def test_mappings_are_paged_by_ascending_candidate_id_within_one_source(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1)
    _stage(store, 2)
    store.record_mappings(_mappings("S0001", "B0001", 5))
    store.record_mappings(_mappings("S0002", "B0002", 3))

    page = store.list_session_mappings("S0001", limit=2)

    assert [row.candidate_id for row in page] == ["B0001-C0000", "B0001-C0001", "B0001-C0002"]
    assert {row.source_session_id for row in page} == {"S0001"}


def test_a_mapping_cursor_resumes_after_its_candidate(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1)
    store.record_mappings(_mappings("S0001", "B0001", 5))

    resumed = store.list_session_mappings("S0001", limit=10, after="B0001-C0001")

    assert [row.candidate_id for row in resumed] == ["B0001-C0002", "B0001-C0003", "B0001-C0004"]


def test_the_mapping_query_seeks_through_the_source_index(conn: sqlite3.Connection) -> None:
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT candidate_id FROM import_candidate_mappings "
        "WHERE source_session_id = ? AND candidate_id > ? ORDER BY candidate_id LIMIT ?",
        ("S0001", "C", 3),
    ).fetchall()

    assert any("idx_import_candidate_mappings_source" in str(row["detail"]) for row in plan)


def test_counts_are_aggregates_over_the_whole_source(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1, candidates=4)
    store.record_mappings(_mappings("S0001", "B0001", 3))
    store.record_mappings(
        [
            CandidateMappingRow(
                candidate_id="B0001-C0009",
                batch_id="B0001",
                source_session_id="S0001",
                disposition=DISPOSITION_MERGED_AWAY,
                entity_type="relationship",
                entity_id=None,
                created_at=_START,
            )
        ]
    )

    totals = store.count_source_candidates("S0001", "B0001")

    assert totals.mappings_total == 4
    assert totals.mappings_by_disposition == (("entity", 3), ("merged_away", 1))
    assert totals.staged_total == 4
    assert totals.staged_by_status == (("pending", 4),)


def test_counts_for_a_source_with_no_batch_report_no_staging(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1)
    store.record_mappings(_mappings("S0001", "B0001", 2))

    totals = store.count_source_candidates("S0001", None)

    assert totals.mappings_total == 2
    assert totals.staged_total == 0
    assert totals.staged_by_status == ()


def test_counts_never_read_a_candidate_body(conn: sqlite3.Connection) -> None:
    """The summary is a `GROUP BY`, so its cost does not grow with what the candidates hold."""
    store = _store(conn)
    _stage(store, 1, candidates=3)

    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT status, COUNT(*) FROM import_staging WHERE batch_id = ? GROUP BY status",
        ("B0001",),
    ).fetchall()

    assert all("candidate_json" not in str(row["detail"]) for row in plan)
    assert store.count_source_candidates("S0001", "B0001").staged_total == 3


def test_getting_an_unknown_session_returns_nothing(conn: sqlite3.Connection) -> None:
    assert _store(conn).get_session("S9999") is None


def test_a_stored_session_round_trips_through_get_session(conn: sqlite3.Connection) -> None:
    store = _store(conn)
    _stage(store, 1, created_at=_START)

    session = store.get_session("S0001")

    assert session is not None
    assert session.source_kind == "linkedin"
    assert session.batch_id == "B0001"
    assert session.created_at == _START
