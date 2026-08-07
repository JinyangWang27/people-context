"""SQLite aggregate implementation of the recency insight port."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from people_context.ports.insights import RecencySignal


def _as_utc(value: datetime) -> datetime:
    """Return one comparable UTC instant for a stored timestamp.

    A naive stored value is read as UTC rather than in the host timezone, which is what
    `astimezone()` on a naive value would silently do.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

# Ordinary disclosure levels. Elevated interactions must not shift recency, so the
# filter lives in SQL: a person with only sensitive/restricted interactions has to
# aggregate to exactly the same row as a person with none at all.
ORDINARY_SENSITIVITIES = {"public": "public", "personal": "personal"}

_PEOPLE_SQL = """
SELECT p.id AS person_id, p.canonical_name AS name
FROM persons p
WHERE p.deleted_at IS NULL AND p.is_self = 0
ORDER BY p.id
"""

# `occurred_at` is stored as the writer's own ISO-8601 text, keeping whatever offset
# the caller supplied, so no text comparison orders these rows correctly:
# `2026-06-02T00:00:00.123400+00:00` sorts after `2026-06-01T19:00:00.123499-05:00`
# although it is the earlier instant, and the two carry different calendar dates, which
# is what the age is measured from. SQLite's own `strftime` normalization only resolves
# to the nearest millisecond, so it cannot separate them either. SQL therefore selects
# the rows and the latest one is chosen here by comparing parsed instants exactly.
_INTERACTIONS_SQL = """
SELECT ip.person_id AS person_id, i.id AS interaction_id, i.occurred_at AS occurred_at
FROM interactions i
JOIN interaction_participants ip ON ip.interaction_id = i.id
JOIN persons p ON p.id = ip.person_id AND p.deleted_at IS NULL AND p.is_self = 0
WHERE i.sensitivity IN (:public, :personal)
ORDER BY ip.person_id, i.id
"""

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
        recency = self._ordinary_recency()
        signals: list[RecencySignal] = []
        for row in self._conn.execute(_PEOPLE_SQL).fetchall():
            person_categories = categories.get(row["person_id"], ())
            if category is not None and category not in person_categories:
                continue
            last_interaction_at, interaction_count = recency.get(row["person_id"], (None, 0))
            signals.append(
                RecencySignal(
                    person_id=row["person_id"],
                    name=row["name"],
                    categories=person_categories,
                    last_interaction_at=last_interaction_at,
                    interaction_count=interaction_count,
                )
            )
        return signals

    def _ordinary_recency(self) -> dict[str, tuple[datetime, int]]:
        """Map each person to their latest ordinary interaction and ordinary count.

        The latest interaction is the greatest parsed instant, with the interaction id
        breaking an exact tie. Comparing instants is what makes differing stored offsets
        safe; the winner is still reported exactly as stored.
        """
        latest: dict[str, datetime] = {}
        counts: dict[str, int] = {}
        best_key: dict[str, tuple[datetime, str]] = {}
        for row in self._conn.execute(_INTERACTIONS_SQL, ORDINARY_SENSITIVITIES).fetchall():
            person_id = row["person_id"]
            counts[person_id] = counts.get(person_id, 0) + 1
            occurred_at = datetime.fromisoformat(row["occurred_at"])
            key = (_as_utc(occurred_at), row["interaction_id"])
            if person_id not in best_key or key > best_key[person_id]:
                best_key[person_id] = key
                latest[person_id] = occurred_at
        return {person_id: (occurred_at, counts[person_id]) for person_id, occurred_at in latest.items()}

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
