"""Migration, index, and concurrency guarantees for the M18 source relations."""

from __future__ import annotations

import sqlite3
import threading
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportSourceStore,
    SqliteImportStagingStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.adapters.sqlite.db import latest_schema_version
from people_context.app.imports import CandidateStager, StageCandidates
from people_context.domain.shared import normalize_name

_MIGRATIONS = "people_context.adapters.sqlite.migrations"
_DIGEST = "a" * 64
_NEW_TABLES = ("import_source_sessions", "import_candidate_mappings")

#: The migration that introduced those relations. Pinned rather than derived from the latest
#: version, so a later additive migration does not silently turn this into a test of itself.
_SOURCE_MIGRATION = 7


def _legacy_database(path: Path, *, through: int) -> None:
    """Write the database a release shipping only the first `through` migrations would."""
    conn = sqlite3.connect(path)
    conn.create_function("people_normalize", 1, normalize_name, deterministic=True)
    try:
        for name in sorted(entry.name for entry in resources.files(_MIGRATIONS).iterdir()):
            if not name.endswith(".sql") or int(name.split("_", 1)[0]) > through:
                continue
            conn.executescript(resources.files(_MIGRATIONS).joinpath(name).read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {through}")
        conn.commit()
    finally:
        conn.close()


def _tables(conn: Any) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _indexes(conn: Any, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})")}  # noqa: S608 - fixed constants


def test_a_fresh_database_creates_both_source_relations(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    assert set(_NEW_TABLES) <= _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_schema_version()


def test_a_legacy_database_upgrades_without_losing_its_staging(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    _legacy_database(path, through=_SOURCE_MIGRATION - 1)
    legacy = sqlite3.connect(path)
    legacy.row_factory = sqlite3.Row
    try:
        assert not set(_NEW_TABLES) & _tables(legacy)
        legacy.execute(
            """INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)
               VALUES ('c1', 'b1', 'import/linkedin', '{"type":"person"}', 'pending',
                       '2026-07-20T12:00:00+00:00')"""
        )
        legacy.commit()
    finally:
        legacy.close()

    upgraded = open_db(path)

    assert set(_NEW_TABLES) <= _tables(upgraded)
    assert upgraded.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 1
    # A pre-M18 batch is left exactly as it was rather than backfilled with a guessed receipt.
    assert upgraded.execute("SELECT COUNT(*) FROM import_source_sessions").fetchone()[0] == 0


def test_the_relations_carry_the_indexes_their_reads_depend_on(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    assert "idx_import_source_sessions_recent" in _indexes(conn, "import_source_sessions")
    mapping_indexes = _indexes(conn, "import_candidate_mappings")
    assert "idx_import_candidate_mappings_source" in mapping_indexes
    assert "idx_import_candidate_mappings_entity" in mapping_indexes


def test_a_mapping_requires_a_source_session(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO import_candidate_mappings
               (candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at)
               VALUES ('c1', 'b1', 'missing', 'entity', 'person', 'p1', '2026-07-20T12:00:00+00:00')"""
        )


@pytest.mark.parametrize(
    "disposition, entity_type, entity_id",
    [
        ("entity", "person", None),
        ("merged_away", "relationship", "r1"),
        ("merged_away", "person", None),
    ],
)
def test_an_impossible_disposition_is_refused_by_the_schema(
    tmp_path: Path,
    disposition: str,
    entity_type: str,
    entity_id: str | None,
) -> None:
    conn = open_db(tmp_path / "people.db")
    conn.execute(
        """INSERT INTO import_source_sessions (id, source_kind, status, created_at)
           VALUES ('s1', 'linkedin', 'staged', '2026-07-20T12:00:00+00:00')"""
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO import_candidate_mappings
               (candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at)
               VALUES ('c1', 'b1', 's1', ?, ?, ?, '2026-07-20T12:00:00+00:00')""",
            (disposition, entity_type, entity_id),
        )


def test_a_redacted_receipt_cannot_keep_caller_metadata(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO import_source_sessions
               (id, source_kind, label, content_digest, status, created_at)
               VALUES ('s1', 'linkedin', 'Interview with Alice', ?, 'redacted',
                       '2026-07-20T12:00:00+00:00')""",
            (_DIGEST,),
        )


def test_a_redacted_receipt_must_retain_its_claim_key(tmp_path: Path) -> None:
    """A terminal receipt exists to make one claim non-restageable.

    Duplicate detection finds it by that key alone, so a redacted row carrying only the digest is
    invisible to the lookup and the forgotten source would stage fresh instead of being refused.
    """
    conn = open_db(tmp_path / "people.db")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO import_source_sessions
               (id, source_kind, content_digest, claim_key, status, created_at)
               VALUES ('s1', 'linkedin', ?, NULL, 'redacted', '2026-07-20T12:00:00+00:00')""",
            (_DIGEST,),
        )


def test_a_claim_without_a_digest_is_refused_by_the_schema(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO import_source_sessions (id, source_kind, claim_key, status, created_at)
               VALUES ('s1', 'linkedin', 'linkedin\x1fx', 'staged', '2026-07-20T12:00:00+00:00')"""
        )


def _stager(path: Path) -> tuple[Any, StageCandidates]:
    conn = open_db(path)
    people = SqlitePeopleRepository(conn)
    staging = SqliteImportStagingStore(conn)
    sources = SqliteImportSourceStore(conn)
    audit = SqliteAuditLog(conn)

    class _Clock:
        def now(self) -> Any:
            from datetime import UTC, datetime

            return datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    return conn, StageCandidates(CandidateStager(people, staging, _Clock(), sources, audit))


def test_two_concurrent_attempts_publish_exactly_one_default_batch(tmp_path: Path) -> None:
    """Both processes race for one claim; the loser writes nothing and sees the winner."""
    path = tmp_path / "people.db"
    open_db(path).close()
    candidates = [{"type": "person", "ref": "a", "name": "Alice Ahmed", "aliases": []}]
    results: list[Any] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _attempt() -> None:
        conn, stage = _stager(path)
        try:
            barrier.wait(timeout=10)
            results.append(
                stage.execute(
                    "weekly-sync",
                    candidates,
                    source_kind="meeting_transcript",
                    content_digest=_DIGEST,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - reported below rather than swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 2
    conn = open_db(path)
    try:
        sessions = conn.execute("SELECT * FROM import_source_sessions").fetchall()
        batches = {row["batch_id"] for row in conn.execute("SELECT DISTINCT batch_id FROM import_staging")}
    finally:
        conn.close()
    assert len(sessions) == 1
    assert len(batches) == 1
    assert sorted(result.duplicate for result in results) == [False, True]
    assert {result.batch_id for result in results} == batches
    assert {result.source_session_id for result in results} == {sessions[0]["id"]}
