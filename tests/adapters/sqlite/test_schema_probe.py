"""The read-only schema probe used to decide whether opening would write."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from people_context.adapters.sqlite import open_db
from people_context.adapters.sqlite.db import latest_schema_version, stored_schema_version
from people_context.domain.shared import normalize_name

_MIGRATIONS = "people_context.adapters.sqlite.migrations"


def _legacy_database(path: Path, *, through: int) -> None:
    """Write a database the way a release shipping only the first `through` migrations would."""
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


def test_an_up_to_date_database_reports_the_shipped_version(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    open_db(db_file).close()

    assert stored_schema_version(db_file) == latest_schema_version()


def test_a_legacy_database_reports_its_own_older_version(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    _legacy_database(db_file, through=latest_schema_version() - 1)

    stored = stored_schema_version(db_file)

    assert stored == latest_schema_version() - 1
    assert stored is not None and stored < latest_schema_version()


def test_probing_creates_nothing_and_migrates_nothing(tmp_path: Path) -> None:
    """The whole point: asking the question must not answer it by changing the database."""
    db_file = tmp_path / "people.db"
    _legacy_database(db_file, through=latest_schema_version() - 1)
    before = db_file.stat().st_mtime_ns

    stored_schema_version(db_file)

    conn = sqlite3.connect(db_file)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_schema_version() - 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
    finally:
        conn.close()
    assert db_file.stat().st_mtime_ns == before
    assert not (tmp_path / "people.db-wal").exists()


def test_an_absent_path_is_unanswerable_and_no_directory_is_created(tmp_path: Path) -> None:
    """`_resolve_target` would have created the parent; the probe must not."""
    missing = tmp_path / "nowhere" / "people.db"

    assert stored_schema_version(missing) is None
    assert not (tmp_path / "nowhere").exists()


def test_a_file_that_is_not_a_database_is_unanswerable(tmp_path: Path) -> None:
    not_a_database = tmp_path / "people.db"
    not_a_database.write_text("this is not a SQLite database", encoding="utf-8")

    assert stored_schema_version(not_a_database) is None


def test_the_in_memory_database_has_no_stored_version() -> None:
    assert stored_schema_version(":memory:") is None


def test_the_shipped_version_matches_the_highest_migration_file() -> None:
    numbers = [
        int(entry.name.split("_", 1)[0])
        for entry in resources.files(_MIGRATIONS).iterdir()
        if entry.name.endswith(".sql")
    ]

    assert latest_schema_version() == max(numbers)
