"""CLI behaviour for the read-only `pctx health` startup check."""

from __future__ import annotations

import hashlib
import importlib.metadata
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.mcp.server import main as server_main
from people_context.adapters.sqlite import open_db
from people_context.adapters.sqlite.db import SQLCIPHER_MODULE, latest_schema_version
from people_context.cli import health
from people_context.config import DB_KEY_ENV


def _snapshot(directory: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


def _database_at_schema(directory: Path, version: int) -> Path:
    db = directory / "people.db"
    open_db(db).close()
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
    return db


def test_server_entrypoint_refuses_without_a_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    old = tmp_path / "old"
    old.mkdir()

    for db in (blocker / "people.db", _database_at_schema(old, 5)):
        with pytest.raises(SystemExit) as refused:
            server_main(["--db", str(db)])
        assert refused.value.code == 2
        assert "pctx health" in capsys.readouterr().err


def test_health_passes_without_touching_an_existing_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "people.db"
    open_db(db).close()
    before = db.read_bytes()

    assert cli.main(["--db", str(db), "health"]) == 0

    output = capsys.readouterr().out
    assert db.read_bytes() == before
    # SQLite's read-only open of a WAL database may leave empty sidecars, but never any content.
    wal = tmp_path / "people.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0
    assert f"[ok] your database: {db}" in output
    assert "using a temporary database, not yours" in output
    assert "[info] client: not checkable" in output


def test_health_does_not_create_a_missing_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "missing" / "people.db"

    assert cli.main(["--db", str(db), "health"]) == 0

    assert "does not exist yet" in capsys.readouterr().out
    assert not db.parent.exists()


def test_health_refuses_someone_elses_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "other.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    before = _snapshot(tmp_path)

    assert cli.main(["--db", str(db), "health"]) == 1

    output = capsys.readouterr().out
    assert "[fail] your database" in output
    assert "Next: Point PEOPLE_CONTEXT_DB" in output
    assert _snapshot(tmp_path) == before


def test_database_check_warns_before_a_migration_without_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This release's only migration is the baseline, so a migratable schema needs a future release.
    current = latest_schema_version()
    db = _database_at_schema(tmp_path, current)
    monkeypatch.setattr(health, "latest_schema_version", lambda: current + 1)

    check = health.check_database(str(db), encrypted=False)

    assert check.status == "warn"
    assert "next server start upgrades it" in check.detail
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == current


def test_database_check_fails_a_schema_the_server_would_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _database_at_schema(tmp_path, 5)

    assert cli.main(["--db", str(db), "health"]) == 1

    output = capsys.readouterr().out
    assert "[fail] your database" in output
    assert "Open it once with people-context 1.3.0" in output
    assert "next server start upgrades" not in output
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_database_check_warns_about_a_newer_schema(tmp_path: Path) -> None:
    db = _database_at_schema(tmp_path, latest_schema_version() + 1)

    check = health.check_database(str(db), encrypted=False)

    assert check.status == "warn"
    assert check.next_action == "Update people-context."


@pytest.mark.parametrize("key", [None, "", "   "])
def test_encrypted_check_needs_a_usable_key_even_for_a_new_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    db = tmp_path / "new.db"
    if key is None:
        monkeypatch.delenv(DB_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(DB_KEY_ENV, key)

    check = health.check_database(str(db), encrypted=True)

    assert check.status == "fail"
    assert check.next_action
    assert not db.exists()


@pytest.mark.parametrize("target", ["new.db", ":memory:"])
def test_encrypted_check_needs_the_binding_even_without_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    monkeypatch.setenv(DB_KEY_ENV, "battery staple")
    monkeypatch.setitem(sys.modules, SQLCIPHER_MODULE, None)
    db = target if target == ":memory:" else str(tmp_path / target)

    check = health.check_database(db, encrypted=True)

    assert check.status == "fail"
    assert "SQLCipher" in check.detail
    assert "battery staple" not in check.detail
    assert not (tmp_path / "new.db").exists()


def test_database_check_reports_an_in_memory_target() -> None:
    assert health.check_database(":memory:", encrypted=False).status == "ok"


def test_database_check_fails_a_read_only_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "people.db"
    open_db(db).close()
    monkeypatch.setattr(health.os, "access", lambda _path, _mode: False)

    check = health.check_database(str(db), encrypted=False)

    assert check.status == "fail"
    assert "read-only" in check.detail


def test_database_check_fails_when_a_new_database_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health.os, "access", lambda _path, _mode: False)

    check = health.check_database(str(tmp_path / "missing" / "people.db"), encrypted=False)

    assert check.status == "fail"
    assert "is not writable" in check.detail
    assert check.next_action


def test_database_check_fails_an_unreadable_file(tmp_path: Path) -> None:
    db = tmp_path / "junk.db"
    db.write_text("not a database, not even close", encoding="utf-8")

    check = health.check_database(str(db), encrypted=False)

    assert check.status == "fail"
    assert "cannot read" in check.detail


def test_dependency_check_names_a_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def version(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(health.importlib.metadata, "version", version)

    check = health.check_dependencies()

    assert check.status == "fail"
    assert "mcp" in check.detail
    assert check.next_action


def test_server_that_cannot_be_launched_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(health, "_server_command", lambda _db: [str(tmp_path / "no-such-executable")])

    check = health.check_server()

    assert check.status == "fail"
    assert "could not start" in check.detail
    assert check.next_action


def test_server_failure_is_one_short_line_with_a_next_action(monkeypatch: pytest.MonkeyPatch) -> None:
    script = (
        "import sys\n"
        "sys.stderr.write('Traceback (most recent call last):\\n' + '  frame\\n' * 500 + 'Boom: ' + 'x' * 1000)\n"
        "sys.exit(1)\n"
    )
    monkeypatch.setattr(health, "_server_command", lambda _db: [sys.executable, "-c", script])

    check = health.check_server()

    assert check.status == "fail"
    assert "Traceback" not in check.detail
    assert "Boom" in check.detail
    assert len(check.detail) <= 200
    assert check.next_action


def test_server_timeout_is_reported_with_a_next_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "_server_command", lambda _db: [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(health, "HANDSHAKE_TIMEOUT_SECONDS", 1)

    check = health.check_server()

    assert check.status == "fail"
    assert "no MCP handshake within 1s" in check.detail
    assert check.next_action


def test_healthy_server_handshake_is_not_flaky() -> None:
    # Regression: closing stdin before the reply let the server cancel `tools/list`.
    for _ in range(3):
        check = health.check_server()
        assert check.status == "ok", check.detail
        assert re.search(r"passed with \d+ tools", check.detail)


def test_missing_uv_warns_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health.shutil, "which", lambda _name: None)

    check = health.check_uv()

    assert check.status == "warn"
    assert check.next_action


def test_server_reports_an_unopenable_database_without_a_traceback(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "people_context.adapters.mcp.server", "--db", str(blocker / "people.db")],
        input="",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "pctx health" in result.stderr
    assert result.stdout == "", "stdout carries only the MCP protocol"
