"""SQLite relationship vocabulary and canonical edge helpers."""

from __future__ import annotations

import sqlite3

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.relationship_vocabulary import RelationshipType, normalize_relationship_type


class SqliteRelationshipVocabularyStore:
    """Read seeded/custom vocabulary and add portable custom rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def resolve(self, value: str) -> RelationshipType | None:
        normalized = normalize_relationship_type(value)
        row = self._conn.execute(
            """
            SELECT rt.*
            FROM relationship_types rt
            LEFT JOIN relationship_type_synonyms s ON s.type = rt.type
            WHERE rt.type = ? OR s.synonym = ?
            ORDER BY CASE WHEN rt.type = ? THEN 0 ELSE 1 END, rt.type
            LIMIT 1
            """,
            (normalized, normalized, normalized),
        ).fetchone()
        return self._hydrate(row) if row is not None else None

    def list_types(self) -> list[RelationshipType]:
        rows = self._conn.execute("SELECT * FROM relationship_types ORDER BY category, type").fetchall()
        return [self._hydrate(row) for row in rows]

    def list_uncategorized_types(self) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT r.type
            FROM relationships r
            LEFT JOIN relationship_types rt ON rt.type = r.type
            JOIN persons s ON s.id = r.subject_id AND s.deleted_at IS NULL
            JOIN persons o ON o.id = r.object_id AND o.deleted_at IS NULL
            WHERE rt.type IS NULL
            ORDER BY r.type
            """
        ).fetchall()
        return [row["type"] for row in rows]

    def add(self, rows: list[RelationshipType]) -> None:
        with SqliteUnitOfWork(self._conn):
            for row in rows:
                self._conn.execute(
                    """
                    INSERT INTO relationship_types (type, inverse, symmetric, category, canonical)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row.type, row.inverse, int(row.symmetric), row.category, int(row.canonical)),
                )
                self._conn.executemany(
                    "INSERT INTO relationship_type_synonyms (synonym, type) VALUES (?, ?)",
                    [(synonym, row.type) for synonym in row.synonyms],
                )

    def _hydrate(self, row: sqlite3.Row) -> RelationshipType:
        synonyms = self._conn.execute(
            "SELECT synonym FROM relationship_type_synonyms WHERE type = ? ORDER BY synonym",
            (row["type"],),
        ).fetchall()
        return RelationshipType(
            type=row["type"],
            inverse=row["inverse"],
            symmetric=bool(row["symmetric"]),
            category=row["category"],
            canonical=bool(row["canonical"]),
            synonyms=[item["synonym"] for item in synonyms],
        )
