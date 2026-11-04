"""CLI behaviour for the read-only `pctx health` startup check."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db
from people_context.cli import health


def _snapshot(directory: Path) -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


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


def test_database_check_warns_before_a_migration_without_running_it(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    open_db(db).close()
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 1")

    check = health.check_database(str(db), encrypted=False)

    assert check.status == "warn"
    assert "next server start upgrades it" in check.detail
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


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
