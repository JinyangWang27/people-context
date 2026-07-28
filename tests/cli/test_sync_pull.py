"""`pctx sync pull` CLI tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    open_db,
)
from people_context.app.exports import SYNC_BUNDLE_FILENAME
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.cli import main

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _seed(db_path: Path, name: str = "Alice") -> None:
    conn = open_db(db_path)
    repository = SqlitePeopleRepository(conn)
    RememberPerson(repository, repository, SqliteAuditLog(conn), _Clock()).execute(RememberPersonInput(name=name))
    conn.close()


def _bundle(tmp_path: Path, capsys) -> Path:
    """Push one real bundle from a seeded source database."""
    source = tmp_path / "source.db"
    outbox = tmp_path / "outbox"
    _seed(source)
    assert main(["--db", str(source), "sync", "push", "--output", str(outbox)]) == 0
    capsys.readouterr()
    return outbox / SYNC_BUNDLE_FILENAME


def test_sync_pull_previews_and_restores_a_bundle_into_a_fresh_database(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path, capsys)
    target = tmp_path / "target.db"

    assert main(["--db", str(target), "sync", "pull", "--input", str(bundle), "--yes"]) == 0

    out = capsys.readouterr().out
    assert "people: 1" in out
    assert "Restored 1 people" in out
    assert "all retired" in out
    assert "Local clock advanced" in out

    assert main(["--db", str(target), "list"]) == 0
    assert "Alice" in capsys.readouterr().out


def test_sync_pull_accepts_the_containing_directory(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path, capsys)

    assert main(["--db", str(tmp_path / "target.db"), "sync", "pull", "--input", str(bundle.parent), "--yes"]) == 0

    assert "Restored 1 people" in capsys.readouterr().out


def test_sync_pull_requires_confirmation_when_yes_is_absent(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path, capsys)
    target = tmp_path / "target.db"
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert main(["--db", str(target), "sync", "pull", "--input", str(bundle)]) == 0

    out = capsys.readouterr().out
    assert "Aborted." in out
    conn = open_db(target)
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    conn.close()


def test_sync_pull_restores_after_an_interactive_yes(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path, capsys)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert main(["--db", str(tmp_path / "target.db"), "sync", "pull", "--input", str(bundle)]) == 0

    assert "Restored 1 people" in capsys.readouterr().out


def test_sync_pull_restores_a_bundle_carrying_an_uncategorized_relationship_type(
    tmp_path: Path,
    capsys,
) -> None:
    """push -> pull must round-trip a stored type that has no vocabulary row."""
    source = tmp_path / "source.db"
    outbox = tmp_path / "outbox"
    conn = open_db(source)
    repository = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    left = RememberPerson(repository, repository, audit, clock).execute(RememberPersonInput(name="Dana"))
    right = RememberPerson(repository, repository, audit, clock).execute(RememberPersonInput(name="Eli"))
    SetRelationship(repository, SqliteRelationshipStore(conn), audit, clock).execute(
        SetRelationshipInput(subject_id=left.person.id, object_id=right.person.id, type="childhood_rival_of")
    )
    conn.close()

    assert main(["--db", str(source), "sync", "push", "--output", str(outbox)]) == 0
    capsys.readouterr()
    target = tmp_path / "target.db"

    assert main(["--db", str(target), "sync", "pull", "--input", str(outbox), "--yes"]) == 0

    assert "Restored 2 people" in capsys.readouterr().out
    conn = open_db(target)
    stored = [row["type"] for row in conn.execute("SELECT type FROM relationships")]
    conn.close()
    assert stored == ["childhood_rival_of"]


def test_sync_pull_refuses_an_invalid_bundle_before_prompting(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path, capsys)
    document = json.loads(bundle.read_text(encoding="utf-8"))
    document["format"] = "people-context-export"
    bundle.write_text(json.dumps(document), encoding="utf-8")

    def _refuse_to_prompt(_prompt: str = "") -> str:
        raise AssertionError("an invalid bundle must never reach the confirmation prompt")

    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    target = tmp_path / "target.db"

    assert main(["--db", str(target), "sync", "pull", "--input", str(bundle)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid_bundle" in captured.err
    assert "No changes were made." in captured.err
    conn = open_db(target)
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    conn.close()


def test_sync_pull_refuses_an_unstorable_integer_without_a_traceback(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An integer SQLite cannot persist must refuse structurally, not raise mid-restore."""
    bundle = _bundle(tmp_path, capsys)
    document = json.loads(bundle.read_text(encoding="utf-8"))
    document["watermark"]["hlc_physical_ms"] = 2**63
    bundle.write_text(json.dumps(document), encoding="utf-8")

    def _refuse_to_prompt(_prompt: str = "") -> str:
        raise AssertionError("an unstorable bundle must never reach the confirmation prompt")

    monkeypatch.setattr("builtins.input", _refuse_to_prompt)
    target = tmp_path / "target.db"

    assert main(["--db", str(target), "sync", "pull", "--input", str(bundle)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid_bundle" in captured.err
    assert "watermark.hlc_physical_ms" in captured.err
    conn = open_db(target)
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    conn.close()


def test_sync_pull_refuses_a_non_baseline_target(tmp_path: Path, capsys) -> None:
    bundle = _bundle(tmp_path, capsys)
    target = tmp_path / "target.db"
    _seed(target, name="Existing")

    assert main(["--db", str(target), "sync", "pull", "--input", str(bundle), "--yes"]) == 1

    captured = capsys.readouterr()
    assert "target_not_empty" in captured.err
    assert "persons: 1 row(s)" in captured.err
    conn = open_db(target)
    assert [row["canonical_name"] for row in conn.execute("SELECT canonical_name FROM persons")] == ["Existing"]
    conn.close()


def test_sync_pull_reports_a_missing_bundle_without_a_traceback(tmp_path: Path, capsys) -> None:
    assert main(["--db", str(tmp_path / "target.db"), "sync", "pull", "--input", str(tmp_path / "nope.json")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Cannot read the sync bundle" in captured.err


def test_sync_pull_reports_non_utf8_content_without_a_traceback(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "bundle.json"
    bundle.write_bytes(b"\xff\xfe\x00binary")

    assert main(["--db", str(tmp_path / "target.db"), "sync", "pull", "--input", str(bundle)]) == 1

    assert "not UTF-8 text" in capsys.readouterr().err


def test_sync_pull_requires_an_input_path(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--db", str(tmp_path / "target.db"), "sync", "pull"])
