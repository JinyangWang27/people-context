"""SQLite storage for canonical relationship edges."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.relationship import Relationship
from people_context.domain.shared import Provenance, ValidityPeriod


class SqliteRelationshipStore:
    """Persist and inspect relationship rows for normalization-aware writes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_relationship(self, relationship: Relationship) -> None:
        with SqliteUnitOfWork(self._conn):
            self._conn.execute(
                """
                INSERT INTO relationships (
                    id, subject_id, object_id, type, label, valid_from, valid_to, confidence,
                    provenance_source, provenance_session, provenance_stated_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    subject_id = excluded.subject_id,
                    object_id = excluded.object_id,
                    type = excluded.type,
                    label = excluded.label,
                    valid_from = excluded.valid_from,
                    valid_to = excluded.valid_to,
                    confidence = excluded.confidence,
                    provenance_source = excluded.provenance_source,
                    provenance_session = excluded.provenance_session,
                    provenance_stated_by = excluded.provenance_stated_by,
                    created_at = excluded.created_at
                """,
                (
                    relationship.id,
                    relationship.subject_id,
                    relationship.object_id,
                    relationship.type,
                    relationship.label,
                    relationship.period.valid_from.isoformat() if relationship.period.valid_from else None,
                    relationship.period.valid_to.isoformat() if relationship.period.valid_to else None,
                    relationship.confidence,
                    relationship.provenance.source,
                    relationship.provenance.session,
                    relationship.provenance.stated_by,
                    relationship.created_at.isoformat(),
                ),
            )

    def find_active_relationship(
        self,
        subject_id: str,
        object_id: str,
        type: str,
        as_of: date,
    ) -> Relationship | None:
        row = self._conn.execute(
            """
            SELECT * FROM relationships
            WHERE subject_id = ? AND object_id = ? AND type = ?
              AND (valid_to IS NULL OR valid_to >= ?)
            ORDER BY created_at, id
            LIMIT 1
            """,
            (subject_id, object_id, type, as_of.isoformat()),
        ).fetchone()
        return _relationship(row) if row is not None else None

    def list_relationships(self) -> list[Relationship]:
        rows = self._conn.execute("SELECT * FROM relationships ORDER BY created_at, id").fetchall()
        return [_relationship(row) for row in rows]

    def delete_relationship(self, relationship_id: str) -> None:
        with SqliteUnitOfWork(self._conn):
            self._conn.execute("DELETE FROM relationships WHERE id = ?", (relationship_id,))


def _relationship(row: sqlite3.Row) -> Relationship:
    return Relationship(
        id=row["id"],
        subject_id=row["subject_id"],
        object_id=row["object_id"],
        type=row["type"],
        label=row["label"],
        period=ValidityPeriod(
            valid_from=date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        ),
        confidence=row["confidence"],
        provenance=Provenance(
            source=row["provenance_source"],
            session=row["provenance_session"],
            stated_by=row["provenance_stated_by"],
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
