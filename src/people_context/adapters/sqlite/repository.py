"""SQLite-backed people repository (PersonReader + PersonWriter)."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import normalize_name
from people_context.ports.repository import SearchHit

# Modest fixed scores for the non-FTS substring fallback path.
_LIKE_SCORE_CANONICAL = 0.5
_LIKE_SCORE_ALIAS = 0.4


class SqlitePeopleRepository:
    """Persist and retrieve Person aggregates in SQLite, maintaining the FTS index."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # -- PersonWriter ----------------------------------------------------

    def save_person(self, person: Person) -> None:
        canonical_norm = normalize_name(person.canonical_name)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO persons (
                    id, canonical_name, canonical_name_normalized, is_self,
                    summary, created_at, updated_at, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    canonical_name_normalized = excluded.canonical_name_normalized,
                    is_self = excluded.is_self,
                    summary = excluded.summary,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    deleted_at = excluded.deleted_at
                """,
                (
                    person.id,
                    person.canonical_name,
                    canonical_norm,
                    int(person.is_self),
                    person.summary,
                    person.created_at.isoformat(),
                    person.updated_at.isoformat(),
                    person.deleted_at.isoformat() if person.deleted_at else None,
                ),
            )

            self._conn.execute("DELETE FROM aliases WHERE person_id = ?", (person.id,))
            self._conn.executemany(
                """
                INSERT INTO aliases (id, person_id, value, value_normalized, kind, lang, script)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        alias.id,
                        person.id,
                        alias.value,
                        normalize_name(alias.value),
                        alias.kind.value,
                        alias.lang,
                        alias.script,
                    )
                    for alias in person.aliases
                ],
            )

            self._refresh_search_rows(person)

    def _refresh_search_rows(self, person: Person) -> None:
        self._conn.execute("DELETE FROM person_search WHERE person_id = ?", (person.id,))
        if person.deleted_at is not None:
            return
        self._conn.executemany(
            "INSERT INTO person_search (name, person_id) VALUES (?, ?)",
            [(name, person.id) for name in person.all_names()],
        )

    def rebuild_person_search(self) -> tuple[int, int]:
        """Rebuild FTS rows from active people and aliases in one transaction."""
        people = self.list_people()
        names = [(name, person.id) for person in people for name in person.all_names()]
        with self._conn:
            self._conn.execute("DELETE FROM person_search")
            self._conn.executemany("INSERT INTO person_search (name, person_id) VALUES (?, ?)", names)
        return len(people), len(names)

    # -- PersonReader ----------------------------------------------------

    def get(self, person_id: str) -> Person | None:
        row = self._conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        return self._hydrate(row) if row is not None else None

    def get_self(self) -> Person | None:
        row = self._conn.execute(
            "SELECT * FROM persons WHERE is_self = 1 AND deleted_at IS NULL LIMIT 1"
        ).fetchone()
        return self._hydrate(row) if row is not None else None

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]:
        sql = "SELECT * FROM persons"
        if not include_deleted:
            sql += " WHERE deleted_at IS NULL"
        sql += " ORDER BY canonical_name"
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        return [self._hydrate(row) for row in self._conn.execute(sql, params).fetchall()]

    def find_by_normalized_name(self, normalized: str) -> list[Person]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT p.id AS id
            FROM persons p
            LEFT JOIN aliases a ON a.person_id = p.id
            WHERE p.deleted_at IS NULL
              AND (p.canonical_name_normalized = ? OR a.value_normalized = ?)
            ORDER BY p.canonical_name
            """,
            (normalized, normalized),
        ).fetchall()
        people = [self.get(row["id"]) for row in rows]
        return [person for person in people if person is not None]

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]:
        normalized = normalize_name(query)
        if not normalized:
            return []
        hits = self._fts_search(normalized, limit)
        if not hits:
            hits = self._like_search(normalized, limit)
        return hits

    # -- search helpers --------------------------------------------------

    def _fts_search(self, normalized_query: str, limit: int) -> list[SearchHit]:
        tokens = normalized_query.split()
        if not tokens:
            return []
        match_expr = " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
        fetch = max(limit * 5, 50)
        rows = self._conn.execute(
            """
            SELECT ps.name AS name, ps.person_id AS person_id, bm25(person_search) AS rank
            FROM person_search ps
            JOIN persons p ON p.id = ps.person_id
            WHERE person_search MATCH ? AND p.deleted_at IS NULL
            ORDER BY rank
            LIMIT ?
            """,
            (match_expr, fetch),
        ).fetchall()
        scored = [(row["name"], row["person_id"], _bm25_to_score(row["rank"])) for row in rows]
        return self._build_hits(scored, limit)

    def _like_search(self, normalized_query: str, limit: int) -> list[SearchHit]:
        pattern = f"%{_escape_like(normalized_query)}%"
        rows = self._conn.execute(
            """
            SELECT p.id AS person_id, p.canonical_name AS name,
                   ? AS score
            FROM persons p
            WHERE p.deleted_at IS NULL AND p.canonical_name_normalized LIKE ? ESCAPE '\\'
            UNION ALL
            SELECT a.person_id AS person_id, a.value AS name, ? AS score
            FROM aliases a
            JOIN persons p ON p.id = a.person_id
            WHERE p.deleted_at IS NULL AND a.value_normalized LIKE ? ESCAPE '\\'
            """,
            (_LIKE_SCORE_CANONICAL, pattern, _LIKE_SCORE_ALIAS, pattern),
        ).fetchall()
        scored = [(row["name"], row["person_id"], float(row["score"])) for row in rows]
        scored.sort(key=lambda item: item[2], reverse=True)
        return self._build_hits(scored, limit)

    def _build_hits(self, scored: list[tuple[str, str, float]], limit: int) -> list[SearchHit]:
        best: dict[str, SearchHit] = {}
        person_cache: dict[str, Person | None] = {}
        for name, person_id, score in scored:
            if person_id not in person_cache:
                person_cache[person_id] = self.get(person_id)
            person = person_cache[person_id]
            if person is None:
                continue
            match_kind = "canonical" if name == person.canonical_name else "alias"
            existing = best.get(person_id)
            if existing is None or score > existing.score:
                best[person_id] = SearchHit(
                    person=person, score=score, matched_value=name, match_kind=match_kind
                )
        hits = sorted(best.values(), key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    # -- hydration -------------------------------------------------------

    def _hydrate(self, row: sqlite3.Row) -> Person:
        alias_rows = self._conn.execute(
            "SELECT id, value, kind, lang, script FROM aliases WHERE person_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
        aliases = [
            Alias(
                id=alias_row["id"],
                value=alias_row["value"],
                kind=AliasKind(alias_row["kind"]),
                lang=alias_row["lang"],
                script=alias_row["script"],
            )
            for alias_row in alias_rows
        ]
        return Person(
            id=row["id"],
            canonical_name=row["canonical_name"],
            is_self=bool(row["is_self"]),
            summary=row["summary"],
            aliases=aliases,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
        )


def _bm25_to_score(rank: float) -> float:
    """Map an FTS5 bm25 value (more negative == better) monotonically into (0, 1)."""
    exponent = max(-60.0, min(60.0, rank))
    return 1.0 / (1.0 + math.exp(exponent))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
