"""The bounded batch measurement the CLI runs before it materializes an existing batch."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.sqlite import SqliteImportStagingStore, open_db
from people_context.adapters.sqlite.import_staging import _MEASURE_BATCH_SQL
from people_context.app.imports import (
    BATCH_TOO_LARGE_FOR_CLI,
    CLI_IMPORT_BUDGET,
    MAX_CLI_STAGED_CANDIDATES,
    MAX_CLI_STAGED_PAYLOAD_BYTES,
    ImportBudget,
    ImportPipelineError,
    PreflightImportBatch,
    ReviewImport,
)

_NOW = datetime(2026, 7, 22, tzinfo=UTC)
_SOURCE = "import/agent:legacy-notes"


def _insert_rows(conn: sqlite3.Connection, batch_id: str, count: int, candidate_json: str) -> None:
    """Insert legacy staging rows directly, as an older uncapped MCP batch would have."""
    conn.executemany(
        """INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        [
            (f"{batch_id}-{index:07d}", batch_id, _SOURCE, candidate_json, _NOW.isoformat())
            for index in range(count)
        ],
    )
    conn.commit()


def _person_json(name: str = "Legacy Person") -> str:
    return json.dumps({"type": "person", "name": name, "aliases": [], "matched_person_id": None})


def test_the_measurement_counts_rows_and_stored_utf8_payload_bytes() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        candidate = _person_json("Amina Haddad — Zürich")
        _insert_rows(conn, "batch", 3, candidate)

        size = staging.measure_batch("batch", row_scan_limit=10)

    expected = 3 * (len(candidate.encode("utf-8")) + len(_SOURCE.encode("utf-8")))
    assert size.row_count == 3
    assert size.payload_bytes == expected
    assert size.truncated is False


def test_the_batch_predicate_seeks_rather_than_scanning_every_staged_row() -> None:
    """Without an index the inner LIMIT bounds the rows returned, not the rows visited.

    That would make review and commit grow with unrelated staging history, which is exactly
    the cost the bounded preflight exists to remove.
    """
    with open_db(":memory:") as conn:
        _insert_rows(conn, "wanted", 2, _person_json())
        _insert_rows(conn, "unrelated", 200, _person_json())

        plan = " ".join(
            str(row["detail"])
            for row in conn.execute(f"EXPLAIN QUERY PLAN {_MEASURE_BATCH_SQL}", ("wanted", 10))
        )
        list_plan = " ".join(
            str(row["detail"])
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM import_staging WHERE batch_id = ? ORDER BY created_at, id",
                ("wanted",),
            )
        )

    assert "SCAN import_staging" not in plan
    assert "idx_import_staging_batch" in plan
    # The same index also carries `list_batch`'s ordering, so the full read loses its sort.
    assert "USE TEMP B-TREE FOR ORDER BY" not in list_plan


def test_an_absent_batch_measures_as_empty() -> None:
    with open_db(":memory:") as conn:
        size = SqliteImportStagingStore(conn).measure_batch("nothing", row_scan_limit=10)

    assert (size.row_count, size.payload_bytes, size.truncated) == (0, 0, False)


def test_the_scan_stops_one_row_past_the_caller_ceiling() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", 12, _person_json())

        size = staging.measure_batch("batch", row_scan_limit=5)

    assert size.row_count == 5
    assert size.truncated is True


def test_marking_rows_committed_does_not_change_the_measured_payload() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", 4, _person_json())
        before = staging.measure_batch("batch", row_scan_limit=10)

        staging.mark_committed([f"batch-{index:07d}" for index in range(4)])

        assert staging.measure_batch("batch", row_scan_limit=10) == before


def test_a_legacy_batch_over_the_row_ceiling_is_refused_before_list_batch() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", MAX_CLI_STAGED_CANDIDATES + 1, _person_json())
        guarded = _NoListBatch(staging)

        with pytest.raises(ImportPipelineError) as refusal:
            PreflightImportBatch(guarded).execute("batch")

    assert refusal.value.code == BATCH_TOO_LARGE_FOR_CLI
    assert refusal.value.details == {"batch_id": "batch", "limit": MAX_CLI_STAGED_CANDIDATES}


def test_a_legacy_batch_exactly_at_the_row_ceiling_is_accepted() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", MAX_CLI_STAGED_CANDIDATES, _person_json())

        size = PreflightImportBatch(staging).execute("batch")

    assert size.row_count == MAX_CLI_STAGED_CANDIDATES
    assert size.truncated is False


def test_a_legacy_batch_over_the_payload_ceiling_is_refused_before_list_batch() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", 65, _person_json("p" * (1024 * 1024)))
        guarded = _NoListBatch(staging)

        with pytest.raises(ImportPipelineError) as refusal:
            PreflightImportBatch(guarded).execute("batch")

    assert refusal.value.code == BATCH_TOO_LARGE_FOR_CLI
    assert refusal.value.details == {"batch_id": "batch", "limit": MAX_CLI_STAGED_PAYLOAD_BYTES}


def test_the_payload_ceiling_is_a_strict_maximum_rather_than_an_exclusive_one() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "batch", 4, _person_json())
        exact = staging.measure_batch("batch", row_scan_limit=10).payload_bytes

        at_limit = PreflightImportBatch(staging, ImportBudget(max_staged_payload_bytes=exact)).execute("batch")
        assert at_limit.payload_bytes == exact

        with pytest.raises(ImportPipelineError) as refusal:
            PreflightImportBatch(staging, ImportBudget(max_staged_payload_bytes=exact - 1)).execute("batch")

    assert refusal.value.code == BATCH_TOO_LARGE_FOR_CLI


def test_an_unknown_batch_stays_the_next_use_cases_answer() -> None:
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)

        assert PreflightImportBatch(staging).execute("absent").row_count == 0

        with pytest.raises(ImportPipelineError) as refusal:
            ReviewImport(staging).execute("absent")

    assert refusal.value.code == "batch_not_found"


def test_an_unbounded_budget_asks_the_store_nothing() -> None:
    preflight = PreflightImportBatch(_ForbiddenMeasure(), ImportBudget())

    assert preflight.execute("batch").row_count == 0


def test_the_cli_refuses_an_oversized_legacy_batch_safely_without_mutating_anything(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    try:
        _insert_rows(conn, "legacy", MAX_CLI_STAGED_CANDIDATES + 1, _person_json())
    finally:
        conn.close()

    for argv in (
        ["import", "review", "legacy"],
        ["import", "review", "legacy", "--json"],
        ["import", "commit", "legacy", "--all"],
        ["import", "commit", "legacy", "--accept", "legacy-0000000"],
    ):
        assert cli.main(["--db", str(db_file), *argv]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "cannot be read by this command" in captured.err
        assert "Legacy Person" not in captured.err

    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
        statuses = {row[0] for row in conn.execute("SELECT DISTINCT status FROM import_staging")}
    finally:
        conn.close()
    assert statuses == {"pending"}


def test_the_released_mcp_review_and_commit_still_read_an_oversized_legacy_batch() -> None:
    """The CLI bound is an adapter bound; it is not retroactively added to the MCP tools."""
    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        _insert_rows(conn, "legacy", MAX_CLI_STAGED_CANDIDATES + 1, _person_json())

        review = ReviewImport(staging).execute("legacy")

    assert len(review.candidates) == MAX_CLI_STAGED_CANDIDATES + 1
    assert CLI_IMPORT_BUDGET.max_candidates == MAX_CLI_STAGED_CANDIDATES


class _NoListBatch:
    """A size reader that fails loudly if the caller materializes the batch anyway."""

    def __init__(self, inner: SqliteImportStagingStore) -> None:
        self._inner = inner

    def measure_batch(self, batch_id: str, *, row_scan_limit: int) -> Any:
        return self._inner.measure_batch(batch_id, row_scan_limit=row_scan_limit)

    def list_batch(self, batch_id: str) -> Any:
        raise AssertionError("the preflight must decide before any full-batch read")


class _ForbiddenMeasure:
    """A size reader that must not be consulted at all."""

    def measure_batch(self, batch_id: str, *, row_scan_limit: int) -> Any:
        raise AssertionError("an unbounded budget has nothing to measure")
