"""CLI export regression tests."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.cli import main

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def test_cli_export_file_is_owner_only(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "export.json"
    conn = open_db(db_path)
    repository = SqlitePeopleRepository(conn)
    RememberPerson(repository, repository, SqliteAuditLog(conn), _Clock()).execute(RememberPersonInput(name="Alice"))
    conn.close()

    assert main(["--db", str(db_path), "export", "--output", str(output)]) == 0
    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    assert "Alice" in output.read_text(encoding="utf-8")
