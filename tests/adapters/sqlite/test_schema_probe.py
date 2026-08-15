"""The read-only schema probe used to decide whether opening would write."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.sqlite import open_db
from people_context.adapters.sqlite.db import (
    SQLCIPHER_MODULE,
    inspect_schema,
    latest_schema_version,
    open_encrypted_db,
)
from people_context.domain.shared import normalize_name

KEY = "correct horse battery staple"
#: A fragment of the key that must never open the database or reach a message.
KEY_SENTINEL = "battery staple"

requires_sqlcipher = pytest.mark.skipif(
    importlib.util.find_spec("sqlcipher3") is None,
    reason="the optional `encrypted` extra is not installed on this platform",
)


def _keyed_connection(path: Path) -> Any:
    """Open an encrypted database directly, without the migrating opener."""
    dbapi = importlib.import_module(SQLCIPHER_MODULE)
    conn = dbapi.connect(str(path))
    conn.execute(f"PRAGMA key = '{KEY}'")
    return conn

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

    probed = inspect_schema(db_file)

    assert probed is not None
    assert probed.version == latest_schema_version()
    assert probed.is_people_context


def test_a_legacy_database_reports_its_own_older_version(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    _legacy_database(db_file, through=latest_schema_version() - 1)

    stored = inspect_schema(db_file)

    assert stored is not None
    assert stored.version == latest_schema_version() - 1
    assert stored.version < latest_schema_version()


def test_probing_creates_nothing_and_migrates_nothing(tmp_path: Path) -> None:
    """The whole point: asking the question must not answer it by changing the database."""
    db_file = tmp_path / "people.db"
    _legacy_database(db_file, through=latest_schema_version() - 1)
    before = db_file.stat().st_mtime_ns

    inspect_schema(db_file)

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

    assert inspect_schema(missing) is None
    assert not (tmp_path / "nowhere").exists()


def test_a_file_that_is_not_a_database_is_unanswerable(tmp_path: Path) -> None:
    not_a_database = tmp_path / "people.db"
    not_a_database.write_text("this is not a SQLite database", encoding="utf-8")

    assert inspect_schema(not_a_database) is None


def test_the_in_memory_database_has_no_stored_version() -> None:
    assert inspect_schema(":memory:") is None


def test_the_shipped_version_matches_the_highest_migration_file() -> None:
    numbers = [
        int(entry.name.split("_", 1)[0])
        for entry in resources.files(_MIGRATIONS).iterdir()
        if entry.name.endswith(".sql")
    ]

    assert latest_schema_version() == max(numbers)


@requires_sqlcipher
def test_an_encrypted_database_answers_with_its_key(tmp_path: Path) -> None:
    """The guard must cover `--encrypted` too, or it is a hole for exactly those stores."""
    db_file = tmp_path / "people.db"
    open_encrypted_db(db_file, KEY).close()

    probed = inspect_schema(db_file, KEY)

    assert probed is not None
    assert probed.version == latest_schema_version()
    assert probed.is_people_context


@requires_sqlcipher
def test_an_encrypted_database_is_unanswerable_with_the_wrong_key(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    open_encrypted_db(db_file, KEY).close()

    assert inspect_schema(db_file, "not the key") is None


@requires_sqlcipher
def test_a_legacy_encrypted_database_reports_its_older_version(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    open_encrypted_db(db_file, KEY).close()
    conn = _keyed_connection(db_file)
    try:
        conn.execute(f"PRAGMA user_version = {latest_schema_version() - 1}")
        conn.commit()
    finally:
        conn.close()

    stored = inspect_schema(db_file, KEY)

    assert stored is not None
    assert stored.version == latest_schema_version() - 1


@requires_sqlcipher
def test_probing_an_encrypted_database_leaves_the_key_out_of_every_message(tmp_path: Path) -> None:
    """A wrong key returns the same `None` as any other unreadable file, carrying no detail."""
    db_file = tmp_path / "people.db"
    open_encrypted_db(db_file, KEY).close()

    assert inspect_schema(db_file, KEY_SENTINEL) is None
    assert inspect_schema(tmp_path / "absent.db", KEY) is None


def test_a_path_containing_uri_syntax_names_the_file_it_means(tmp_path: Path) -> None:
    """`?` and `#` are ordinary in a filename and syntax in a SQLite URI."""
    weird = tmp_path / "people?context#1.db"
    open_db(weird).close()

    probed = inspect_schema(weird)

    assert probed is not None
    assert probed.version == latest_schema_version()
    assert probed.is_people_context


def test_probing_a_uri_syntax_path_that_is_absent_creates_no_sibling(tmp_path: Path) -> None:
    """Interpolating the path dropped `mode=ro` into the query and created `people` read-write."""
    weird = tmp_path / "people?context.db"

    assert inspect_schema(weird) is None
    assert list(tmp_path.iterdir()) == []


def test_an_unrelated_sqlite_database_is_not_this_project(tmp_path: Path) -> None:
    """`user_version` is any application's to set, so it cannot identify a store on its own."""
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    try:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute(f"PRAGMA user_version = {latest_schema_version() + 4}")
        conn.commit()
    finally:
        conn.close()

    probed = inspect_schema(other)

    assert probed is not None
    assert probed.version >= latest_schema_version()
    assert not probed.is_people_context


def test_a_partial_schema_is_not_this_project_either(tmp_path: Path) -> None:
    """One identifying table is not enough; a half-built file must not be opened normally."""
    partial = tmp_path / "partial.db"
    conn = sqlite3.connect(partial)
    try:
        conn.execute("CREATE TABLE persons (id TEXT PRIMARY KEY)")
        conn.execute(f"PRAGMA user_version = {latest_schema_version()}")
        conn.commit()
    finally:
        conn.close()

    probed = inspect_schema(partial)

    assert probed is not None
    assert not probed.is_people_context
