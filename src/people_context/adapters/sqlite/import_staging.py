"""SQLite import staging persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.imports import StagedImportRow


class SqliteImportStagingStore:
    """Persist import candidate batches without retaining source content."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def stage_batch(self, rows: list[StagedImportRow]) -> None:
        with SqliteUnitOfWork(self._conn):
            self._conn.executemany(
                """INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row.id,
                        row.batch_id,
                        row.source,
                        json.dumps(row.candidate, ensure_ascii=False),
                        row.status,
                        row.created_at.isoformat(),
                    )
                    for row in rows
                ],
            )

    def list_batch(self, batch_id: str) -> list[StagedImportRow]:
        rows = self._conn.execute(
            "SELECT * FROM import_staging WHERE batch_id = ? ORDER BY created_at, id",
            (batch_id,),
        ).fetchall()
        return [
            StagedImportRow(
                id=row["id"],
                batch_id=row["batch_id"],
                source=row["source"],
                candidate=json.loads(row["candidate_json"]),
                status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def mark_committed(self, candidate_ids: list[str]) -> None:
        if not candidate_ids:
            return
        with SqliteUnitOfWork(self._conn):
            self._conn.executemany(
                "UPDATE import_staging SET status = 'committed' WHERE id = ? AND status = 'pending'",
                [(candidate_id,) for candidate_id in candidate_ids],
            )
