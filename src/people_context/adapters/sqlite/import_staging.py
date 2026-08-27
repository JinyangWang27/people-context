"""SQLite import staging persistence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.imports import StagedBatchSize, StagedImportRow

#: Measures the batch a bounded caller is about to read without loading a single candidate
#: body. `LENGTH(CAST(... AS BLOB))` is the stored UTF-8 byte count, which is exactly what a
#: reader has to materialize, and the inner `LIMIT` stops the scan one row past the caller's
#: ceiling so an oversized batch costs a bounded query rather than a full one.
_MEASURE_BATCH_SQL = """
    SELECT COUNT(*) AS row_count, COALESCE(SUM(payload_bytes), 0) AS payload_bytes
    FROM (
        SELECT LENGTH(CAST(candidate_json AS BLOB)) + LENGTH(CAST(source AS BLOB)) AS payload_bytes
        FROM import_staging
        WHERE batch_id = ?
        LIMIT ?
    )
"""


class SqliteImportStagingStore:
    """Persist import candidate batches without retaining source content."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a join-safe transaction boundary so batch commits are atomic."""
        return SqliteUnitOfWork(self._conn)

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

    def measure_batch(self, batch_id: str, *, row_scan_limit: int) -> StagedBatchSize:
        """Return the batch's row count and persisted reviewable payload bytes.

        A staging batch is append-closed once it is created, so this measurement cannot be
        raced by later growth; marking rows committed changes status, never payload.
        """
        row = self._conn.execute(_MEASURE_BATCH_SQL, (batch_id, row_scan_limit)).fetchone()
        row_count = int(row["row_count"])
        return StagedBatchSize(
            row_count=row_count,
            payload_bytes=int(row["payload_bytes"]),
            truncated=row_count >= row_scan_limit,
        )

    def mark_committed(self, candidate_ids: list[str]) -> None:
        if not candidate_ids:
            return
        with SqliteUnitOfWork(self._conn):
            self._conn.executemany(
                "UPDATE import_staging SET status = 'committed' WHERE id = ? AND status = 'pending'",
                [(candidate_id,) for candidate_id in candidate_ids],
            )
