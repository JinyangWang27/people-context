"""Opt-in SQLCipher connection tests.

Refusals that happen before the optional binding is touched are asserted
everywhere; the tests that actually encrypt pages need the binding and skip on
platforms the `encrypted` extra does not publish a wheel for.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite.db import (
    EncryptedDatabaseError,
    open_db,
    open_encrypted_db,
)
from people_context.app.people.remember import RememberPersonInput
from people_context.config import DB_KEY_ENV

KEY = "correct horse battery staple"
#: A fragment of the key that must never appear in a file, stream, or stored row.
KEY_SENTINEL = "battery staple"

requires_sqlcipher = pytest.mark.skipif(
    importlib.util.find_spec("sqlcipher3") is None,
    reason="the optional `encrypted` extra is not installed on this platform",
)


def _insert_person(conn: sqlite3.Connection, person_id: str, name: str) -> None:
    conn.execute(
        """INSERT INTO persons
           (id, canonical_name, canonical_name_normalized, created_at, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""",
        (person_id, name, name.lower()),
    )


# -- refusals that never reach the binding ------------------------------------


@pytest.mark.parametrize("key", ["", " ", "\t\n "])
def test_empty_or_whitespace_key_is_refused_without_creating_a_file(tmp_path: Path, key: str) -> None:
    target = tmp_path / "people.db"

    with pytest.raises(EncryptedDatabaseError):
        open_encrypted_db(target, key)

    assert not target.exists()


def test_missing_binding_explains_the_extra_without_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_binding(name: str) -> object:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr("importlib.import_module", _no_binding)
    target = tmp_path / "people.db"

    with pytest.raises(EncryptedDatabaseError) as exc_info:
        open_encrypted_db(target, KEY)

    assert "people-context[encrypted]" in str(exc_info.value)
    assert not target.exists()


# -- encrypted lifecycle ------------------------------------------------------


@requires_sqlcipher
def test_migrations_run_after_keying_and_seed_one_local_device(tmp_path: Path) -> None:
    conn = open_encrypted_db(tmp_path / "people.db", KEY)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM devices WHERE retired_at IS NULL").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        # The migration helper is registered on the encrypted connection too.
        assert conn.execute("SELECT people_normalize('  Ada  LOVELACE ')").fetchone()[0] == "ada lovelace"
    finally:
        conn.close()


@requires_sqlcipher
def test_reopening_with_the_correct_key_reads_previously_written_rows(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    conn = open_encrypted_db(path, KEY)
    _insert_person(conn, "p1", "Ada")
    conn.commit()
    device_id = conn.execute("SELECT id FROM devices").fetchone()[0]
    conn.close()

    reopened = open_encrypted_db(path, KEY)
    try:
        assert reopened.execute("SELECT canonical_name FROM persons").fetchone()[0] == "Ada"
        # Reopening an existing database must not mint a second local identity.
        assert [row[0] for row in reopened.execute("SELECT id FROM devices")] == [device_id]
    finally:
        reopened.close()


@requires_sqlcipher
def test_key_with_quotes_round_trips(tmp_path: Path) -> None:
    tricky = "it's a \"quoted'' key\"; -- not SQL"
    path = tmp_path / "people.db"
    conn = open_encrypted_db(path, tricky)
    _insert_person(conn, "p1", "Ada")
    conn.commit()
    conn.close()

    reopened = open_encrypted_db(path, tricky)
    try:
        assert reopened.execute("SELECT canonical_name FROM persons").fetchone()[0] == "Ada"
    finally:
        reopened.close()

    with pytest.raises(EncryptedDatabaseError):
        open_encrypted_db(path, tricky.replace("not SQL", "other"))


@requires_sqlcipher
def test_wrong_key_is_refused_with_a_generic_message(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    open_encrypted_db(path, KEY).close()

    with pytest.raises(EncryptedDatabaseError) as exc_info:
        open_encrypted_db(path, "some other passphrase")

    message = str(exc_info.value)
    assert "key is wrong" in message
    assert KEY_SENTINEL not in message
    # The driver error is not chained, so no page detail reaches a traceback.
    assert exc_info.value.__cause__ is None


@requires_sqlcipher
def test_plaintext_database_is_refused_rather_than_silently_reopened(tmp_path: Path) -> None:
    plain = tmp_path / "plain.db"
    open_db(plain).close()

    with pytest.raises(EncryptedDatabaseError):
        open_encrypted_db(plain, KEY)


@requires_sqlcipher
def test_plain_sqlite_cannot_read_schema_or_data(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    conn = open_encrypted_db(path, KEY)
    _insert_person(conn, "p1", "Ada")
    conn.commit()
    conn.close()

    plain = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            plain.execute("SELECT name FROM sqlite_master").fetchall()
    finally:
        plain.close()


@requires_sqlcipher
def test_wal_and_shm_companions_reveal_no_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    conn = open_encrypted_db(path, KEY)
    try:
        _insert_person(conn, "p1", "Grace Hopper SENTINEL")
        conn.commit()

        companions = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]
        assert [companion.name for companion in companions if companion.exists()] == [
            "people.db-wal",
            "people.db-shm",
        ], "WAL mode must leave both companion files in place while the connection is open"
        for candidate in [path, *companions]:
            payload = candidate.read_bytes()
            assert b"SENTINEL" not in payload, f"{candidate.name} exposes record text"
            assert KEY_SENTINEL.encode() not in payload, f"{candidate.name} exposes key material"
    finally:
        conn.close()


@requires_sqlcipher
def test_key_material_never_reaches_streams_audit_or_changelog(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "people.db"
    conn = open_encrypted_db(path, KEY)
    _insert_person(conn, "p1", "Ada")
    conn.commit()
    conn.close()

    with pytest.raises(EncryptedDatabaseError):
        open_encrypted_db(path, "another passphrase entirely")

    captured = capfd.readouterr()
    assert KEY_SENTINEL not in captured.out
    assert KEY_SENTINEL not in captured.err

    reopened = open_encrypted_db(path, KEY)
    try:
        for table in ("audit_log", "changelog"):
            rows = reopened.execute(f"SELECT * FROM {table}").fetchall()
            assert all(KEY_SENTINEL not in str(tuple(row)) for row in rows)
    finally:
        reopened.close()


# -- composed runtime ---------------------------------------------------------


@requires_sqlcipher
def test_runtime_writes_through_the_ordinary_audit_seam_on_an_encrypted_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "people.db"
    monkeypatch.setenv(DB_KEY_ENV, KEY)

    runtime = build_runtime(path, encrypted=True)
    try:
        runtime.use_cases.remember_person.execute(RememberPersonInput(name="Ada Lovelace"))
        assert runtime.conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1
        assert runtime.conn.execute("SELECT count(*) FROM changelog").fetchone()[0] == 1
    finally:
        runtime.close()

    reopened = build_runtime(path, encrypted=True)
    try:
        candidates = reopened.use_cases.search_people.execute("Ada")
        assert [candidate.canonical_name for candidate in candidates] == ["Ada Lovelace"]
    finally:
        reopened.close()

    assert b"Ada Lovelace" not in path.read_bytes()


@requires_sqlcipher
def test_runtime_without_the_flag_still_opens_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "people.db"
    monkeypatch.setenv(DB_KEY_ENV, KEY)

    runtime = build_runtime(path)
    try:
        assert runtime.conn.execute("PRAGMA user_version").fetchone()[0] > 0
    finally:
        runtime.close()

    # A key in the environment must not encrypt anything on its own.
    plain = sqlite3.connect(path)
    try:
        assert plain.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    finally:
        plain.close()
