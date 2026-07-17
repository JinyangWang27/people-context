"""SQLite-backed append-only audit log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from people_context.ports.audit_log import AuditEntry


class SqliteAuditLog:
    """Append-only audit log persisted in the `audit_log` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, entry: AuditEntry) -> None:
        with self._conn:
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
