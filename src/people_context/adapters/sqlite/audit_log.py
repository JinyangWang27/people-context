"""SQLite-backed append-only audit log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime

from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.hlc import SqliteHybridLogicalClock
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.audit_log import AuditEntry


class SqliteAuditLog:
    """Append-only audit log persisted in the `audit_log` table."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        failure_hook: Callable[[str], None] | None = None,
        *,
        changelog_failure_hook: Callable[[str], None] | None = None,
        wall_clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._conn = conn
        self._failure_hook = failure_hook
        self._changelog = SqliteChangelog(conn, changelog_failure_hook)
        self._hybrid_clock = SqliteHybridLogicalClock(conn, wall_clock_ms)

    @property
    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a join-safe transaction boundary for application orchestration."""
        return SqliteUnitOfWork(self._conn)

    @property
    def changelog(self) -> SqliteChangelog:
        """Return the replay log paired with this accountability log."""
        return self._changelog

    @property
    def hybrid_clock(self) -> SqliteHybridLogicalClock:
        """Return the persisted installation HLC used for changelog ordering."""
        return self._hybrid_clock

    def append(self, entry: AuditEntry) -> None:
        with SqliteUnitOfWork(self._conn):
            if self._failure_hook is not None:
                self._failure_hook("before_append")
            self._conn.execute(
                """
                INSERT INTO audit_log (id, ts, op, entity_type, entity_id, payload_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.ts.isoformat(),
                    entry.op,
                    entry.entity_type,
                    entry.entity_id,
                    json.dumps(entry.payload),
                    entry.source,
                ),
            )

    def list_entries(self, limit: int = 100) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            AuditEntry(
                id=row["id"],
                ts=datetime.fromisoformat(row["ts"]),
                op=row["op"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                payload=json.loads(row["payload_json"]),
                source=row["source"],
            )
            for row in rows
        ]
