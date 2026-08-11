"""SQLite evidence queries behind the data-quality report.

Every query here answers "which stored rows could be a problem", never "which rows are one".
Normalized comparison columns are the ones the repository already maintains, so a collision the
doctor reports is a collision identity resolution would also see, and no second normalization
rule can drift away from the first.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from people_context.ports.curation import (
    AFFILIATION_REFERENCE,
    ALIAS_SOURCE_PREFIX,
    CANONICAL_NAME_SOURCE,
    INTERACTION_REFERENCE,
    RELATIONSHIP_REFERENCE,
    DeletedPersonReference,
    FactAssertion,
    NameUsage,
    PersonRef,
)

HANDLE_KIND = "handle"

# Handle material is a person's addressable identity, so a shared one is reported on its own
# terms; `value_normalized` is the same normalization the alias index is built on.
_SHARED_HANDLES_SQL = f"""
WITH handles AS (
    SELECT a.person_id AS person_id,
           a.id AS alias_id,
           a.value AS value,
           a.value_normalized AS normalized
    FROM aliases a
    JOIN persons p ON p.id = a.person_id AND p.deleted_at IS NULL
    WHERE a.kind = '{HANDLE_KIND}' AND a.value_normalized <> ''
)
SELECT h.person_id, h.value, h.normalized, p.canonical_name, p.is_self
FROM handles h
JOIN persons p ON p.id = h.person_id
WHERE h.normalized IN (
    SELECT normalized FROM handles GROUP BY normalized HAVING COUNT(DISTINCT person_id) > 1
)
ORDER BY h.normalized, h.person_id, h.alias_id
"""

# Non-handle name material: the canonical name plus every alias that is not a handle. A person
# colliding with only themselves (an alias equal to their own canonical name) is not a finding,
# which is what the DISTINCT person count excludes.
_SHARED_NAMES_SQL = f"""
WITH names AS (
    SELECT p.id AS person_id,
           '' AS alias_id,
           p.canonical_name AS value,
           p.canonical_name_normalized AS normalized,
           '{CANONICAL_NAME_SOURCE}' AS source
    FROM persons p
    WHERE p.deleted_at IS NULL AND p.canonical_name_normalized <> ''
    UNION ALL
    SELECT a.person_id,
           a.id,
           a.value,
           a.value_normalized,
           '{ALIAS_SOURCE_PREFIX}' || a.kind
    FROM aliases a
    JOIN persons p ON p.id = a.person_id AND p.deleted_at IS NULL
    WHERE a.kind <> '{HANDLE_KIND}' AND a.value_normalized <> ''
)
SELECT n.person_id, n.value, n.normalized, n.source, p.canonical_name, p.is_self
FROM names n
JOIN persons p ON p.id = n.person_id
WHERE n.normalized IN (
    SELECT normalized FROM names GROUP BY normalized HAVING COUNT(DISTINCT person_id) > 1
)
ORDER BY n.normalized, n.person_id, n.source, n.alias_id
"""

# Candidate contradictions: an active person holding more than one distinct value for the same
# predicate. Values are compared exactly as stored — the reader invents no case or whitespace
# folding of its own — and whether the periods actually overlap is decided by the application.
_CONFLICTING_FACTS_SQL = """
SELECT f.id AS fact_id,
       f.person_id AS person_id,
       f.predicate AS predicate,
       f.value AS value,
       f.sensitivity AS sensitivity,
       f.valid_from AS valid_from,
       f.valid_to AS valid_to,
       p.canonical_name AS canonical_name,
       p.is_self AS is_self
FROM facts f
JOIN persons p ON p.id = f.person_id AND p.deleted_at IS NULL
WHERE EXISTS (
    SELECT 1
    FROM facts other
    WHERE other.person_id = f.person_id
      AND other.predicate = f.predicate
      AND other.value <> f.value
)
ORDER BY f.person_id, f.predicate, f.id
"""

_DELETED_RELATIONSHIP_REFS_SQL = """
SELECT r.id AS entity_id, p.id AS person_id, p.canonical_name AS canonical_name, p.is_self AS is_self
FROM relationships r
JOIN persons p ON p.id IN (r.subject_id, r.object_id) AND p.deleted_at IS NOT NULL
ORDER BY p.id, r.id
"""

_DELETED_AFFILIATION_REFS_SQL = """
SELECT a.id AS entity_id, p.id AS person_id, p.canonical_name AS canonical_name, p.is_self AS is_self
FROM affiliations a
JOIN persons p ON p.id = a.person_id AND p.deleted_at IS NOT NULL
ORDER BY p.id, a.id
"""

_DELETED_PARTICIPANT_REFS_SQL = """
SELECT ip.interaction_id AS entity_id, p.id AS person_id, p.canonical_name AS canonical_name, p.is_self AS is_self
FROM interaction_participants ip
JOIN persons p ON p.id = ip.person_id AND p.deleted_at IS NOT NULL
ORDER BY p.id, ip.interaction_id
"""


class SqliteCurationReader:
    """Read candidate data-quality evidence from the local SQLite store."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_shared_handles(self) -> list[NameUsage]:
        """Return handle usages whose normalized value at least two active people share."""
        return [
            NameUsage(
                person=_person(row),
                value=row["value"],
                normalized=row["normalized"],
                source=f"{ALIAS_SOURCE_PREFIX}{HANDLE_KIND}",
            )
            for row in self._conn.execute(_SHARED_HANDLES_SQL).fetchall()
        ]

    def list_shared_names(self) -> list[NameUsage]:
        """Return non-handle name usages whose normalized value at least two active people share."""
        return [
            NameUsage(
                person=_person(row),
                value=row["value"],
                normalized=row["normalized"],
                source=row["source"],
            )
            for row in self._conn.execute(_SHARED_NAMES_SQL).fetchall()
        ]

    def list_conflicting_facts(self) -> list[FactAssertion]:
        """Return facts of active people whose (person, predicate) group holds differing values."""
        return [
            FactAssertion(
                person=_person(row),
                fact_id=row["fact_id"],
                predicate=row["predicate"],
                value=row["value"],
                sensitivity=row["sensitivity"],
                valid_from=_as_date(row["valid_from"]),
                valid_to=_as_date(row["valid_to"]),
            )
            for row in self._conn.execute(_CONFLICTING_FACTS_SQL).fetchall()
        ]

    def list_deleted_person_references(self) -> list[DeletedPersonReference]:
        """Return every relationship, affiliation, or interaction still pointing at a deleted person."""
        references: list[DeletedPersonReference] = []
        for sql, entity_type in (
            (_DELETED_RELATIONSHIP_REFS_SQL, RELATIONSHIP_REFERENCE),
            (_DELETED_AFFILIATION_REFS_SQL, AFFILIATION_REFERENCE),
            (_DELETED_PARTICIPANT_REFS_SQL, INTERACTION_REFERENCE),
        ):
            references.extend(
                DeletedPersonReference(
                    person=_person(row),
                    entity_type=entity_type,
                    entity_id=row["entity_id"],
                )
                for row in self._conn.execute(sql).fetchall()
            )
        return references


def _person(row: sqlite3.Row) -> PersonRef:
    return PersonRef(
        person_id=row["person_id"],
        name=row["canonical_name"],
        is_self=bool(row["is_self"]),
    )


def _as_date(value: str | None) -> date | None:
    """Parse a stored ISO date, treating an empty or unparseable bound as unbounded."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
