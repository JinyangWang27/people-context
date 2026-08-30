"""SQLite projection behind the bounded person consolidation context.

Three narrow reads rather than one union, because the three record types answer three different
questions and each carries its own page. A shared page would let one dense collection — a person
with four hundred imported observations — consume the whole budget and leave their facts and traits
looking as though there were none to consolidate.

Every read is bounded in SQL, ordered by the exact UTC key `_projection.sort_key` builds, and
filtered by the disclosure levels the caller passes before the page is cut. That last point matters
more here than on a plain listing: the application computes duplicate/contradiction signals *over*
the page it receives, so a record filtered after the cut would be able to change what the caller is
told without ever appearing in what the caller was shown.

Trait evidence is delegated to the timeline reader rather than re-spelled. The citation contract —
the *cited record's* own level decides whether a trait may name it — is one rule, and one rule
deserves one query.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.adapters.sqlite._projection import (
    DATE_START_OF_DAY,
    levels,
    placeholders,
    sort_key,
    source_session,
)
from people_context.adapters.sqlite.timeline_reader import SqlitePersonTimelineReader
from people_context.domain.shared import Provenance, Sensitivity
from people_context.ports.consolidation import (
    ConsolidationFactRow,
    ConsolidationObservationRow,
    ConsolidationTraitRow,
)
from people_context.ports.timeline import (
    ENTRY_FACT,
    ENTRY_OBSERVATION,
    ENTRY_TRAIT,
    TimelineEvidenceRow,
)

# A fact is placed by the date it asserts it began to hold, and by the time it was recorded when it
# asserts none — the same placement the M19.1 timeline uses, so one `limit` describes one window
# across both reads instead of two a caller has to reconcile.
_FACT_ORDER_COLUMN = (
    f"CASE WHEN f.valid_from IS NULL THEN f.recorded_at ELSE f.valid_from || '{DATE_START_OF_DAY}' END"
)

_FACTS_SQL = f"""
SELECT f.id AS fact_id,
       f.predicate AS predicate,
       f.value AS value,
       f.valid_from AS valid_from,
       f.valid_to AS valid_to,
       f.recorded_at AS recorded_at,
       f.confidence AS confidence,
       f.sensitivity AS sensitivity,
       f.provenance_source AS provenance_source,
       f.provenance_session AS provenance_session,
       f.provenance_stated_by AS provenance_stated_by,
       {source_session(ENTRY_FACT, "f.id")} AS source_session_id
FROM facts f
WHERE f.person_id = :person_id AND f.sensitivity IN ({{levels}})
ORDER BY {sort_key(_FACT_ORDER_COLUMN)} DESC, f.id ASC
LIMIT :limit
"""

_TRAITS_SQL = f"""
SELECT t.id AS trait_id,
       t.category AS category,
       t.value AS value,
       t.evidence_note AS evidence_note,
       t.confidence AS confidence,
       t.updated_at AS updated_at,
       t.sensitivity AS sensitivity,
       t.provenance_source AS provenance_source,
       t.provenance_session AS provenance_session,
       t.provenance_stated_by AS provenance_stated_by,
       {source_session(ENTRY_TRAIT, "t.id")} AS source_session_id
FROM traits t
WHERE t.person_id = :person_id AND t.sensitivity IN ({{levels}})
ORDER BY {sort_key("t.updated_at")} DESC, t.id ASC
LIMIT :limit
"""

_OBSERVATIONS_SQL = f"""
SELECT o.id AS observation_id,
       o.text AS text,
       o.observed_at AS observed_at,
       o.sensitivity AS sensitivity,
       o.provenance_source AS provenance_source,
       o.provenance_session AS provenance_session,
       o.provenance_stated_by AS provenance_stated_by,
       {source_session(ENTRY_OBSERVATION, "o.id")} AS source_session_id
FROM observations o
WHERE o.person_id = :person_id AND o.sensitivity IN ({{levels}})
ORDER BY {sort_key("o.observed_at")} DESC, o.id ASC
LIMIT :limit
"""


class SqlitePersonConsolidationReader:
    """Read one person's facts, traits, and observations as bounded maintenance evidence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._timeline = SqlitePersonTimelineReader(conn)

    def list_consolidation_facts(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationFactRow]:
        """Return the person's newest disclosable facts, reading one row past `limit`."""
        rows = self._select(_FACTS_SQL, person_id, limit, sensitivities)
        return [
            ConsolidationFactRow(
                fact_id=row["fact_id"],
                predicate=row["predicate"],
                value=row["value"],
                valid_from=_date(row["valid_from"]),
                valid_to=_date(row["valid_to"]),
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                confidence=float(row["confidence"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
                source_session_id=row["source_session_id"],
            )
            for row in rows
        ]

    def list_consolidation_traits(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationTraitRow]:
        """Return the person's most recently updated disclosable traits, one row past `limit`."""
        rows = self._select(_TRAITS_SQL, person_id, limit, sensitivities)
        return [
            ConsolidationTraitRow(
                trait_id=row["trait_id"],
                category=row["category"],
                value=row["value"],
                evidence_note=row["evidence_note"],
                confidence=float(row["confidence"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
                source_session_id=row["source_session_id"],
            )
            for row in rows
        ]

    def list_consolidation_observations(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationObservationRow]:
        """Return the person's newest disclosable observations, reading one row past `limit`."""
        rows = self._select(_OBSERVATIONS_SQL, person_id, limit, sensitivities)
        return [
            ConsolidationObservationRow(
                observation_id=row["observation_id"],
                text=row["text"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
                source_session_id=row["source_session_id"],
            )
            for row in rows
        ]

    def list_trait_evidence(
        self,
        trait_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[TimelineEvidenceRow]:
        """Return one trait's disclosable citations, on the timeline's own citation contract."""
        return self._timeline.list_trait_evidence(trait_id, limit=limit, sensitivities=sensitivities)

    def _select(
        self,
        sql: str,
        person_id: str,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[sqlite3.Row]:
        bound_levels = levels(sensitivities)
        parameters: dict[str, object] = {"person_id": person_id, "limit": limit + 1, **bound_levels}
        return list(self._conn.execute(sql.format(levels=placeholders(bound_levels)), parameters).fetchall())


def _provenance(row: sqlite3.Row) -> Provenance:
    """Rebuild one record's stored provenance, the same three columns every record type keeps."""
    return Provenance(
        source=row["provenance_source"],
        session=row["provenance_session"],
        stated_by=row["provenance_stated_by"],
    )


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)
