"""Explicit SQLite unit-of-work atomicity tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, SqliteUnitOfWork, open_db
from people_context.app import RememberPerson, RememberPersonInput
from people_context.ports.clock import Clock


class _Clock(Clock):
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_plain_write_rolls_back_when_audit_append_fails() -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)

    def fail(checkpoint: str) -> None:
        assert checkpoint == "before_append"
        raise RuntimeError("injected audit failure")

    use_case = RememberPerson(repo, repo, SqliteAuditLog(conn, fail), _Clock())

    with pytest.raises(RuntimeError, match="injected audit failure"):
        use_case.execute(RememberPersonInput(name="Atomic Alice"))

    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM person_search").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_nested_adapter_boundaries_do_not_commit_outer_transaction() -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)

    with pytest.raises(RuntimeError, match="abort outer"), SqliteUnitOfWork(conn):
        RememberPerson(repo, repo, SqliteAuditLog(conn), _Clock()).execute(RememberPersonInput(name="Nested Alice"))
        raise RuntimeError("abort outer")

    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
