"""CLI behaviour for the aggregate-only `pctx stats` report."""

from __future__ import annotations

import json
import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.sqlite.db import latest_schema_version
from people_context.app.records import RecordFact, RecordFactInput
from people_context.config import DB_KEY_ENV, EXPORT_ENV, SENSITIVE_CONTEXT_ENV
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Sensitivity, normalize_name
from people_context.ports.clock import SystemClock
from people_context.ports.stats import DOCUMENTED_TABLES, STORAGE_FILE

_MIGRATIONS = "people_context.adapters.sqlite.migrations"


def _seed(db_path: Path) -> Person:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        ada = Person(
            canonical_name="Ada Lovelace",
            aliases=[Alias(value="ada", kind=AliasKind.HANDLE)],
        )
        repo.save_person(Person(canonical_name="Me", is_self=True))
        repo.save_person(ada)
        RecordFact(repo, SqliteRecordStore(conn), SqliteAuditLog(conn), SystemClock()).execute(
            RecordFactInput(
                person_id=ada.id,
                predicate="city",
                value="a private address",
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        return ada
    finally:
        conn.close()


def test_stats_reports_the_inventory_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stats"])

    out = capsys.readouterr().out
    assert code == 0
    assert "People:   2 active, 0 soft-deleted, 1 self" in out
    assert "Tables" in out
    assert "Alias kinds" in out
    assert "Changelog entries by device" in out


def test_the_human_report_never_prints_a_stored_personal_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    ada = _seed(db_file)

    cli.main(["--db", str(db_file), "stats"])

    out = capsys.readouterr().out
    assert "Ada Lovelace" not in out
    assert "a private address" not in out
    assert ada.id not in out
    # The sensitivity bucket name is schema vocabulary, not the value it labels.
    assert "restricted" in out


def test_the_path_is_redacted_by_default_and_included_only_on_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "stats"])
    redacted = capsys.readouterr().out
    cli.main(["--db", str(db_file), "stats", "--include-path"])
    included = capsys.readouterr().out

    assert str(db_file) not in redacted
    assert "Database:" not in redacted
    assert f"Database: {db_file}" in included


def test_the_json_document_redacts_the_path_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "stats", "--json"])
    redacted = json.loads(capsys.readouterr().out)
    cli.main(["--db", str(db_file), "stats", "--json", "--include-path"])
    included = json.loads(capsys.readouterr().out)

    assert redacted["database_path"] is None
    assert included["database_path"] == str(db_file)


def test_gate_booleans_come_from_this_process_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    monkeypatch.delenv(SENSITIVE_CONTEXT_ENV, raising=False)
    monkeypatch.delenv(EXPORT_ENV, raising=False)
    cli.main(["--db", str(db_file), "stats", "--json"])
    closed = json.loads(capsys.readouterr().out)

    monkeypatch.setenv(SENSITIVE_CONTEXT_ENV, "1")
    monkeypatch.setenv(EXPORT_ENV, "true")
    cli.main(["--db", str(db_file), "stats", "--json"])
    opened = json.loads(capsys.readouterr().out)

    assert closed["environment"] == {"sensitive_context": False, "export": False}
    assert opened["environment"] == {"sensitive_context": True, "export": True}


def test_an_unrecognized_gate_value_does_not_count_as_enabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI must agree with the MCP server about what "enabled" means."""
    db_file = tmp_path / "people.db"
    _seed(db_file)
    monkeypatch.setenv(SENSITIVE_CONTEXT_ENV, "maybe")

    cli.main(["--db", str(db_file), "stats", "--json"])

    assert json.loads(capsys.readouterr().out)["environment"]["sensitive_context"] is False


def test_the_json_document_is_the_whole_of_stdout_and_the_notice_goes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stats", "--json"])

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0
    assert document["format"] == "people-context-stats"
    assert document["version"] == 1
    assert [entry["key"] for entry in document["tables"]] == list(DOCUMENTED_TABLES)
    assert "inspect it before sharing" in captured.err.casefold()


def test_the_disclosure_notice_precedes_the_human_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "stats"])

    out = capsys.readouterr().out
    assert out.splitlines()[0] == (
        "This report carries counts only, never stored personal values, but how much you record "
        "about whom is itself revealing. Inspect it before sharing it anywhere."
    )
    assert out.index("Inspect it before sharing") < out.index("People:")


def test_storage_reports_the_main_file_and_its_wal_companions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "stats", "--json"])

    storage = json.loads(capsys.readouterr().out)["storage"]
    assert storage["storage_kind"] == STORAGE_FILE
    assert storage["database_bytes"] == storage["main_bytes"] + storage["wal_bytes"] + storage["shm_bytes"]


def test_an_in_memory_database_says_so_rather_than_reporting_zero_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["--db", ":memory:", "stats"])

    out = capsys.readouterr().out
    assert "Storage:  memory (no measurable file)" in out
    assert "0 bytes" not in out


def test_an_empty_distribution_says_so_explicitly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    SqlitePeopleRepository(conn).save_person(Person(canonical_name="Me", is_self=True))
    conn.close()

    cli.main(["--db", str(db_file), "stats"])

    out = capsys.readouterr().out
    assert "Observations by sensitivity\n  (none)" in out


def test_the_report_writes_nothing_to_the_store(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)
    conn = open_db(db_file)
    before = (
        conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
    )
    conn.close()

    assert cli.main(["--db", str(db_file), "stats"]) == 0

    conn = open_db(db_file)
    after = (
        conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
    )
    conn.close()
    assert after == before


def test_an_absent_database_is_refused_rather_than_created_and_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The mistyped `--db`: the runtime's create-if-absent bootstrap would answer with a
    # database, a device row, and a WAL that this very command had just brought into being.
    missing = tmp_path / "typo" / "people.db"

    code = cli.main(["--db", str(missing), "stats"])

    captured = capsys.readouterr()
    assert code == 1
    assert not missing.exists()
    assert not missing.parent.exists()
    assert captured.out == ""
    assert "no database" in captured.err
    assert str(missing) in captured.err


def test_an_existing_store_is_still_reported_without_gaining_a_device_row(tmp_path: Path) -> None:
    # The refusal is about absence only: a store that already exists must still be measured,
    # and measuring it must not register a second installation identity.
    db_file = tmp_path / "people.db"
    _seed(db_file)
    conn = open_db(db_file)
    before = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conn.close()

    assert cli.main(["--db", str(db_file), "stats"]) == 0

    conn = open_db(db_file)
    after = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    conn.close()
    assert after == before


def test_repeated_runs_produce_an_identical_document_apart_from_its_timestamp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "stats", "--json"])
    first = json.loads(capsys.readouterr().out)
    cli.main(["--db", str(db_file), "stats", "--json"])
    second = json.loads(capsys.readouterr().out)

    del first["generated_at"], second["generated_at"]
    # Storage bytes are the one figure a concurrent WAL checkpoint can legitimately move.
    del first["storage"], second["storage"]
    assert first == second


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


def test_a_legacy_database_is_refused_rather_than_migrated_and_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Existing is not the same as up to date: opening an older store applies migrations."""
    db_file = tmp_path / "people.db"
    _legacy_database(db_file, through=latest_schema_version() - 1)

    code = cli.main(["--db", str(db_file), "stats"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "schema upgrade" in captured.err
    conn = sqlite3.connect(db_file)
    try:
        # A schema upgrade, a journal rewrite, and a device row the report would then count.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_schema_version() - 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
    finally:
        conn.close()
    assert not (tmp_path / "people.db-wal").exists()


def test_a_target_that_is_not_a_database_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    not_a_database = tmp_path / "people.db"
    not_a_database.write_text("this is not a SQLite database", encoding="utf-8")

    code = cli.main(["--db", str(not_a_database), "stats"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "cannot read a database" in captured.err
    # The refusal names the path but never echoes a driver error.
    assert "sqlite3" not in captured.err.casefold()
    assert not_a_database.read_text(encoding="utf-8") == "this is not a SQLite database"


def test_an_up_to_date_database_is_measured_normally(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The guard refuses only what opening would change; an ordinary store is unaffected."""
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stats"])

    assert code == 0
    assert "People:   2 active" in capsys.readouterr().out


def test_encrypted_stats_without_a_key_refuses_before_touching_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard needs the key to inspect an encrypted store, and refuses rather than guess."""
    db_file = tmp_path / "people.db"
    _seed(db_file)
    monkeypatch.delenv(DB_KEY_ENV, raising=False)

    code = cli.main(["--db", str(db_file), "--encrypted", "stats"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert DB_KEY_ENV in captured.err
    # The refusal explains itself with the variable name only, never key material.
    assert "plaintext is never used as a fallback" in captured.err


def test_encrypted_stats_with_a_blank_key_is_refused_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)
    monkeypatch.setenv(DB_KEY_ENV, "   ")

    code = cli.main(["--db", str(db_file), "--encrypted", "stats"])

    assert code == 2
    assert capsys.readouterr().out == ""


def test_an_unrelated_database_is_refused_rather_than_opened_and_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`user_version` is any application's to set, so it cannot admit a file on its own."""
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    try:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('someone elses data')")
        conn.execute(f"PRAGMA user_version = {latest_schema_version() + 4}")
        conn.commit()
    finally:
        conn.close()

    code = cli.main(["--db", str(other), "stats"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "not a people-context database" in captured.err
    # A refusal, not a traceback: opening it rewrote the journal and then failed on `devices`.
    assert "Traceback" not in captured.err
    conn = sqlite3.connect(other)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT body FROM notes").fetchone()[0] == "someone elses data"
    finally:
        conn.close()
    assert not (tmp_path / "other.db-wal").exists()


def test_a_database_whose_name_contains_uri_syntax_is_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`?` is ordinary in a POSIX filename; the guard must not read it as URI syntax."""
    weird = tmp_path / "people?context.db"
    _seed(weird)

    code = cli.main(["--db", str(weird), "stats"])

    assert code == 0
    assert "People:   2 active" in capsys.readouterr().out
    # The interpolated URI silently opened a sibling named `people` instead.
    assert not (tmp_path / "people").exists()
