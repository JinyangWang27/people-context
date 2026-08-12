"""CLI behaviour for the aggregate-only `pctx stats` report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.records import RecordFact, RecordFactInput
from people_context.config import EXPORT_ENV, SENSITIVE_CONTEXT_ENV
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock
from people_context.ports.stats import DOCUMENTED_TABLES, STORAGE_FILE


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


def test_the_json_document_redacts_the_path_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_the_disclosure_notice_precedes_the_human_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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


def test_an_empty_distribution_says_so_explicitly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
