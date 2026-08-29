"""SQLite projection behind the bounded person timeline.

One `UNION ALL` over the six record types a person's history is made of, ordered once and cut by
`LIMIT`. The database returns a page; nothing here hands the application a table to slice.

Three details in the query are deliberate.

**The order key is normalized inside SQLite.** Stored timestamps keep whatever offset the writer
supplied and some are naive, so no text comparison orders them: `2026-06-02T00:00:00+00:00` sorts
after `2026-06-01T19:00:00-05:00` in text while being the *earlier* instant. `strftime` converts
each value to UTC before comparing, and a naive value is read as UTC — the same reading the domain
helper applies — never in the host timezone. The application then re-orders the returned page
exactly, comparing parsed instants at full precision, so SQLite's millisecond resolution decides
only which rows are on the page and never how they read: two records inside one millisecond may
swap places at the page boundary, and are exactly ordered once there.

**A record's source is a scalar subquery, not a join.** M18.1 allows several candidates to map to
one reused entity, so joining the mapping table would multiply a record into as many timeline
entries as imports touched it. The subquery names the earliest mapping instead — the import that
first produced the record — with the candidate id breaking an exact tie.

**Disclosure filtering happens here, not after.** The caller passes the levels it may disclose and
the page is selected from those rows only. Filtering an already-cut page would return a short page
that silently withheld the fact that something was filtered out.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.domain.shared import Sensitivity
from people_context.ports.timeline import (
    BASIS_CREATED_AT,
    BASIS_OBSERVED_AT,
    BASIS_OCCURRED_AT,
    BASIS_RECORDED_AT,
    BASIS_UPDATED_AT,
    BASIS_VALID_FROM,
    ENTRY_AFFILIATION,
    ENTRY_FACT,
    ENTRY_INTERACTION,
    ENTRY_OBSERVATION,
    ENTRY_RELATIONSHIP,
    ENTRY_TRAIT,
    TimelineEvidenceRow,
    TimelineRow,
)

#: A date-only `valid_from` becomes this instant, the same deterministic convention M9.2 fixed for
#: all-day calendar values. The entry still carries the date, so the granularity is never lost.
_DATE_START_OF_DAY = "T00:00:00+00:00"

def _source_session(entry_type: str, id_column: str) -> str:
    """Return the subquery naming the earliest import that produced one durable entity.

    Both arguments are fixed constants of this module — an entry type from the ports vocabulary
    and a column of the branch being composed — never caller input, so the composed text carries
    nothing a caller supplied. `person_id` and the disclosure levels remain bound parameters.
    """
    return (
        "(SELECT m.source_session_id FROM import_candidate_mappings m "
        f"WHERE m.entity_type = '{entry_type}' AND m.entity_id = {id_column} "
        "ORDER BY m.created_at, m.candidate_id LIMIT 1)"
    )


_INTERACTIONS = f"""
SELECT '{ENTRY_INTERACTION}' AS entry_type,
       i.id AS entry_id,
       i.occurred_at AS effective_at,
       '{BASIS_OCCURRED_AT}' AS basis,
       i.summary AS summary,
       i.channel AS detail,
       i.sensitivity AS sensitivity,
       NULL AS valid_from,
       NULL AS valid_to,
       {_source_session(ENTRY_INTERACTION, "i.id")} AS source_session_id
FROM interactions i
JOIN interaction_participants ip ON ip.interaction_id = i.id AND ip.person_id = :person_id
WHERE i.sensitivity IN ({{levels}})
"""

_OBSERVATIONS = f"""
SELECT '{ENTRY_OBSERVATION}' AS entry_type,
       o.id AS entry_id,
       o.observed_at AS effective_at,
       '{BASIS_OBSERVED_AT}' AS basis,
       o.text AS summary,
       NULL AS detail,
       o.sensitivity AS sensitivity,
       NULL AS valid_from,
       NULL AS valid_to,
       {_source_session(ENTRY_OBSERVATION, "o.id")} AS source_session_id
FROM observations o
WHERE o.person_id = :person_id AND o.sensitivity IN ({{levels}})
"""

# A fact asserts when it began to hold, so it is placed there when it says; otherwise it is placed
# at the time it was recorded and `basis` says so.
_FACTS = f"""
SELECT '{ENTRY_FACT}' AS entry_type,
       f.id AS entry_id,
       CASE WHEN f.valid_from IS NULL THEN f.recorded_at ELSE f.valid_from || '{_DATE_START_OF_DAY}' END
           AS effective_at,
       CASE WHEN f.valid_from IS NULL THEN '{BASIS_RECORDED_AT}' ELSE '{BASIS_VALID_FROM}' END AS basis,
       f.predicate AS summary,
       f.value AS detail,
       f.sensitivity AS sensitivity,
       f.valid_from AS valid_from,
       f.valid_to AS valid_to,
       {_source_session(ENTRY_FACT, "f.id")} AS source_session_id
FROM facts f
WHERE f.person_id = :person_id AND f.sensitivity IN ({{levels}})
"""

# Affiliations and relationships carry no stored disclosure level, so they are unfiltered here and
# reported with a null sensitivity rather than one this read invented for them.
_AFFILIATIONS = f"""
SELECT '{ENTRY_AFFILIATION}' AS entry_type,
       a.id AS entry_id,
       CASE WHEN a.valid_from IS NULL THEN a.created_at ELSE a.valid_from || '{_DATE_START_OF_DAY}' END
           AS effective_at,
       CASE WHEN a.valid_from IS NULL THEN '{BASIS_CREATED_AT}' ELSE '{BASIS_VALID_FROM}' END AS basis,
       a.role AS summary,
       org.name AS detail,
       NULL AS sensitivity,
       a.valid_from AS valid_from,
       a.valid_to AS valid_to,
       {_source_session(ENTRY_AFFILIATION, "a.id")} AS source_session_id
FROM affiliations a
JOIN organizations org ON org.id = a.org_id
WHERE a.person_id = :person_id
"""

# An edge is rendered from this person's side, by exactly the rule person context uses: the stored
# type when they are its subject or the type is symmetric, otherwise the vocabulary's inverse. A
# stored `parent_of` shown unchanged on the object's timeline would state the relationship
# backwards. The counterpart is named the way person context names it, and an edge to a
# soft-deleted person is omitted for the same reason: a removed identity does not reappear here.
_RELATIONSHIPS = f"""
SELECT '{ENTRY_RELATIONSHIP}' AS entry_type,
       r.id AS entry_id,
       CASE WHEN r.valid_from IS NULL THEN r.created_at ELSE r.valid_from || '{_DATE_START_OF_DAY}' END
           AS effective_at,
       CASE WHEN r.valid_from IS NULL THEN '{BASIS_CREATED_AT}' ELSE '{BASIS_VALID_FROM}' END AS basis,
       CASE
            WHEN r.subject_id = :person_id THEN r.type
            WHEN rt.symmetric = 1 THEN r.type
            WHEN rt.inverse IS NULL THEN r.type
            ELSE rt.inverse
       END AS summary,
       other.canonical_name AS detail,
       NULL AS sensitivity,
       r.valid_from AS valid_from,
       r.valid_to AS valid_to,
       {_source_session(ENTRY_RELATIONSHIP, "r.id")} AS source_session_id
FROM relationships r
JOIN persons other
  ON other.id = CASE WHEN r.subject_id = :person_id THEN r.object_id ELSE r.subject_id END
 AND other.deleted_at IS NULL
LEFT JOIN relationship_types rt ON rt.type = r.type
WHERE r.subject_id = :person_id OR r.object_id = :person_id
"""

_TRAITS = f"""
SELECT '{ENTRY_TRAIT}' AS entry_type,
       t.id AS entry_id,
       t.updated_at AS effective_at,
       '{BASIS_UPDATED_AT}' AS basis,
       t.category AS summary,
       t.value AS detail,
       t.sensitivity AS sensitivity,
       NULL AS valid_from,
       NULL AS valid_to,
       {_source_session(ENTRY_TRAIT, "t.id")} AS source_session_id
FROM traits t
WHERE t.person_id = :person_id AND t.sensitivity IN ({{levels}})
"""

_BRANCHES = (_INTERACTIONS, _OBSERVATIONS, _FACTS, _AFFILIATIONS, _RELATIONSHIPS, _TRAITS)

# `COALESCE` keeps a value SQLite cannot normalize orderable by its own text instead of sinking it
# below every other row — where `LIMIT` would drop it from a page it belongs on. Every timestamp
# this project writes is `datetime.isoformat()` output, which SQLite does normalize, so the
# fallback is a safety net rather than a path the supported writers reach.
_TIMELINE_SQL = (
    "SELECT entry_type, entry_id, effective_at, basis, summary, detail, sensitivity, "
    "valid_from, valid_to, source_session_id FROM ("
    + " UNION ALL ".join(_BRANCHES)
    + ") ORDER BY COALESCE(strftime('%Y-%m-%dT%H:%M:%fZ', effective_at), effective_at) DESC, "
    "entry_type ASC, entry_id ASC LIMIT :limit"
)

# The cited record's own level decides whether a trait may name it, so it is read alongside the
# link. `evidence_type` selects which table answers; a link whose record is gone reports no level
# and the application withholds it.
_TRAIT_EVIDENCE_SQL = f"""
SELECT te.evidence_type AS evidence_type,
       te.evidence_id AS evidence_id,
       CASE te.evidence_type
            WHEN '{ENTRY_OBSERVATION}' THEN o.sensitivity
            WHEN '{ENTRY_INTERACTION}' THEN i.sensitivity
       END AS sensitivity
FROM trait_evidence te
LEFT JOIN observations o ON te.evidence_type = '{ENTRY_OBSERVATION}' AND o.id = te.evidence_id
LEFT JOIN interactions i ON te.evidence_type = '{ENTRY_INTERACTION}' AND i.id = te.evidence_id
WHERE te.trait_id = :trait_id
ORDER BY te.evidence_type, te.evidence_id
LIMIT :limit
"""


class SqlitePersonTimelineReader:
    """Project one person's durable records into bounded chronological pages."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_timeline_rows(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[TimelineRow]:
        """Return the newest rows for one person, reading one row past `limit`."""
        levels = {f"level{index}": level.value for index, level in enumerate(sensitivities)}
        placeholders = ", ".join(f":{name}" for name in levels) or "NULL"
        sql = _TIMELINE_SQL.format(levels=placeholders)
        parameters: dict[str, object] = {"person_id": person_id, "limit": limit + 1, **levels}
        return [_row(row) for row in self._conn.execute(sql, parameters).fetchall()]

    def list_trait_evidence(self, trait_id: str, *, limit: int) -> list[TimelineEvidenceRow]:
        """Return one trait's evidence citations in stable order, reading one row past `limit`."""
        rows = self._conn.execute(
            _TRAIT_EVIDENCE_SQL,
            {"trait_id": trait_id, "limit": limit + 1},
        ).fetchall()
        return [
            TimelineEvidenceRow(
                evidence_type=row["evidence_type"],
                evidence_id=row["evidence_id"],
                sensitivity=_sensitivity(row["sensitivity"]),
            )
            for row in rows
        ]


def _row(row: sqlite3.Row) -> TimelineRow:
    return TimelineRow(
        entry_type=row["entry_type"],
        entry_id=row["entry_id"],
        effective_at=datetime.fromisoformat(row["effective_at"]),
        basis=row["basis"],
        summary=row["summary"],
        detail=row["detail"],
        sensitivity=_sensitivity(row["sensitivity"]),
        valid_from=_date(row["valid_from"]),
        valid_to=_date(row["valid_to"]),
        source_session_id=row["source_session_id"],
    )


def _sensitivity(value: str | None) -> Sensitivity | None:
    return None if value is None else Sensitivity(value)


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)
