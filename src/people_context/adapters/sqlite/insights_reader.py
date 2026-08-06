"""SQLite aggregate implementation of the recency insight port."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.ports.insights import RecencySignal

# Ordinary disclosure levels. Elevated interactions must not shift recency, so the
# filter lives in SQL: a person with only sensitive/restricted interactions has to
# aggregate to exactly the same row as a person with none at all.
ORDINARY_SENSITIVITIES = {"public": "public", "personal": "personal"}

# `occurred_at` is stored as the writer's own ISO-8601 text, so a caller may record
# `2026-06-01T23:30:00-05:00` alongside `2026-06-02T02:00:00+00:00`. Plain TEXT
# comparison would call the second one later even though the first is the later
# instant, so the latest interaction is chosen by a UTC-normalized sort key. The
# selected row still reports its stored value, at full precision: the normalized key
# orders, it never replaces. `strftime` returns NULL for text SQLite cannot parse,
# and NULL sorts last under DESC, so an unparseable row can never win.
_UTC_SORT_KEY = "strftime('%Y-%m-%dT%H:%M:%fZ', i.occurred_at)"

_RECENCY_SQL = f"""
SELECT p.id AS person_id,
       p.canonical_name AS name,
       (SELECT i.occurred_at
          FROM interactions i
          JOIN interaction_participants ip ON ip.interaction_id = i.id
         WHERE ip.person_id = p.id AND i.sensitivity IN (:public, :personal)
         ORDER BY {_UTC_SORT_KEY} DESC, i.id DESC
         LIMIT 1) AS last_interaction_at,
       (SELECT COUNT(*)
          FROM interactions i
          JOIN interaction_participants ip ON ip.interaction_id = i.id
         WHERE ip.person_id = p.id AND i.sensitivity IN (:public, :personal)) AS interaction_count
FROM persons p
WHERE p.deleted_at IS NULL AND p.is_self = 0
ORDER BY p.id
"""  # noqa: S608 - the interpolated fragment is a module constant; all values remain bound

_CATEGORIES_SQL = """
SELECT CASE WHEN r.subject_id = :self_id THEN r.object_id ELSE r.subject_id END AS person_id,
       COALESCE(rt.category, 'uncategorized') AS category
FROM relationships r
JOIN persons other
  ON other.id = CASE WHEN r.subject_id = :self_id THEN r.object_id ELSE r.subject_id END
 AND other.deleted_at IS NULL
LEFT JOIN relationship_types rt ON rt.type = r.type
WHERE (r.subject_id = :self_id OR r.object_id = :self_id)
  AND (r.valid_from IS NULL OR r.valid_from <= :as_of)
  AND (r.valid_to IS NULL OR r.valid_to >= :as_of)
ORDER BY category, person_id
"""


class SqliteRecencyReader:
    """Aggregate ordinary-disclosure interaction recency for active people."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_recency_signals(self, *, as_of: date, category: str | None = None) -> list[RecencySignal]:
        """Return one signal per active non-self person, optionally filtered by category."""
        categories = self._categories_to_self(as_of)
        signals: list[RecencySignal] = []
        for row in self._conn.execute(_RECENCY_SQL, ORDINARY_SENSITIVITIES).fetchall():
            person_categories = categories.get(row["person_id"], ())
            if category is not None and category not in person_categories:
                continue
            last_interaction_at = row["last_interaction_at"]
            signals.append(
                RecencySignal(
                    person_id=row["person_id"],
                    name=row["name"],
                    categories=person_categories,
                    last_interaction_at=(
                        datetime.fromisoformat(last_interaction_at) if last_interaction_at is not None else None
                    ),
                    interaction_count=int(row["interaction_count"]),
                )
            )
        return signals

    def _categories_to_self(self, as_of: date) -> dict[str, tuple[str, ...]]:
        """Map each counterpart person to their deduplicated relationship-to-self categories."""
        self_row = self._conn.execute(
            "SELECT id FROM persons WHERE is_self = 1 AND deleted_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if self_row is None:
            return {}
        rows = self._conn.execute(
            _CATEGORIES_SQL,
            {"self_id": self_row["id"], "as_of": as_of.isoformat()},
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            person_id = row["person_id"]
            if person_id == self_row["id"]:
                continue
            categories = grouped.setdefault(person_id, [])
            if row["category"] not in categories:
                categories.append(row["category"])
        return {person_id: tuple(categories) for person_id, categories in grouped.items()}
