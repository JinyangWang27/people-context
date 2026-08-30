"""SQLite projection behind the bounded person timeline.

One `UNION ALL` over the six record types a person's history is made of. Every branch is cut to one
page before the union, and the union is cut again, so the database returns a page; nothing here
hands the application a table to slice.

Four details in the query are deliberate. Two of them — the exact UTC ordering key and the
scalar-subquery source lookup — are shared with the consolidation projection and documented in
`_projection.py`, which is the single place either is spelled.

**The order key is normalized, and exact to the microsecond**, so a page is cut by the instant a
timestamp denotes rather than by the text it happens to be stored as.

**Each branch is bounded before the union.** A `LIMIT` applied only to the compound would still let
SQLite emit and sort every matching record of an active person's history to answer `--limit 1`. Each
branch instead orders by that same key and takes one page, so no read ever sorts or materializes
more than one page per record type. The whole page must be a subset of the union of the per-branch
pages, which is what makes cutting early safe.

**A record's source is a scalar subquery, not a join**, so an entity several imports committed onto
stays one timeline entry naming the earliest.

**Disclosure filtering happens here, not after.** The caller passes the levels it may disclose and
the page is selected from those rows only. Filtering an already-cut page would return a short page
that silently withheld the fact that something was filtered out.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.adapters.sqlite._projection import (
    DATE_START_OF_DAY as _DATE_START_OF_DAY,
)
from people_context.adapters.sqlite._projection import (
    levels as _levels,
)
from people_context.adapters.sqlite._projection import (
    placeholders as _placeholders,
)
from people_context.adapters.sqlite._projection import (
    sort_key as _sort_key,
)
from people_context.adapters.sqlite._projection import (
    source_session as _source_session,
)
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

#: The one ordering every level of this query uses, so a branch and the union agree on what "newest"
#: means and the application's own exact sort reproduces it. `entry_type` and `entry_id` break an
#: exact instant tie the way the application does.
_ORDER_BY = f"ORDER BY {_sort_key('effective_at')} DESC, entry_type ASC, entry_id ASC"

#: One branch, already cut to a page. The inner `SELECT *` is what lets the ordering refer to the
#: branch's computed `effective_at` by name instead of repeating each branch's own CASE expression.
_BOUNDED_BRANCHES = tuple(f"SELECT * FROM (SELECT * FROM ({branch}) {_ORDER_BY} LIMIT :limit)" for branch in _BRANCHES)

_TIMELINE_SQL = (
    "SELECT entry_type, entry_id, effective_at, basis, summary, detail, sensitivity, "
    "valid_from, valid_to, source_session_id FROM ("
    + " UNION ALL ".join(_BOUNDED_BRANCHES)
    + f") {_ORDER_BY} LIMIT :limit"
)

# The cited record's own level decides whether a trait may name it, so the filter is here, in the
# same read: a link whose evidence the caller may not see never reaches the application, and so can
# neither be named nor counted towards a truncation signal that would prove it exists.
# `evidence_type` selects which table answers, and a link whose record is gone matches no level and
# is excluded — failing closed rather than naming an id whose record is unaccounted for.
_TRAIT_EVIDENCE_SQL = f"""
SELECT te.evidence_type AS evidence_type,
       te.evidence_id AS evidence_id
FROM trait_evidence te
LEFT JOIN observations o ON te.evidence_type = '{ENTRY_OBSERVATION}' AND o.id = te.evidence_id
LEFT JOIN interactions i ON te.evidence_type = '{ENTRY_INTERACTION}' AND i.id = te.evidence_id
WHERE te.trait_id = :trait_id
  AND CASE te.evidence_type
           WHEN '{ENTRY_OBSERVATION}' THEN o.sensitivity
           WHEN '{ENTRY_INTERACTION}' THEN i.sensitivity
      END IN ({{levels}})
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
        levels = _levels(sensitivities)
        sql = _TIMELINE_SQL.format(levels=_placeholders(levels))
        parameters: dict[str, object] = {"person_id": person_id, "limit": limit + 1, **levels}
        return [_row(row) for row in self._conn.execute(sql, parameters).fetchall()]

    def list_trait_evidence(
        self,
        trait_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[TimelineEvidenceRow]:
        """Return one trait's disclosable citations in stable order, reading one row past `limit`."""
        levels = _levels(sensitivities)
        parameters: dict[str, object] = {"trait_id": trait_id, "limit": limit + 1, **levels}
        rows = self._conn.execute(_TRAIT_EVIDENCE_SQL.format(levels=_placeholders(levels)), parameters).fetchall()
        return [
            TimelineEvidenceRow(evidence_type=row["evidence_type"], evidence_id=row["evidence_id"])
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
