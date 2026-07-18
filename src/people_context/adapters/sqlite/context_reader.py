"""SQLite read adapter for person relationships and contextual records."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.context import AffiliationRecord, RelationshipRecord


class SqliteContextReader:
    """Hydrate contextual domain records from the existing SQLite schema."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_active_relationships(self, person_id: str, as_of: date) -> list[RelationshipRecord]:
        rows = self._conn.execute(
            """
            SELECT r.*, other.id AS other_person_id, other.canonical_name AS other_person_name,
                   rt.inverse AS inverse_type, rt.symmetric AS relationship_symmetric
            FROM relationships r
            JOIN persons other
              ON other.id = CASE WHEN r.subject_id = ? THEN r.object_id ELSE r.subject_id END
            LEFT JOIN relationship_types rt ON rt.type = r.type
            WHERE (r.subject_id = ? OR r.object_id = ?)
              AND (r.valid_to IS NULL OR r.valid_to >= ?)
              AND other.deleted_at IS NULL
            ORDER BY r.created_at DESC, r.id
            """,
            (person_id, person_id, person_id, as_of.isoformat()),
        ).fetchall()
        return [
            RelationshipRecord(
                relationship=Relationship(
                    id=row["id"],
                    subject_id=row["subject_id"],
                    object_id=row["object_id"],
                    type=row["type"],
                    label=row["label"],
                    period=_period(row),
                    confidence=row["confidence"],
                    provenance=_provenance(row),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                other_person_id=row["other_person_id"],
                other_person_name=row["other_person_name"],
                display_type=_display_type(row, person_id),
            )
            for row in rows
        ]

    def list_active_affiliations(self, person_id: str, as_of: date) -> list[AffiliationRecord]:
        rows = self._conn.execute(
            """
            SELECT a.*, o.name AS organization_name
            FROM affiliations a
            JOIN organizations o ON o.id = a.org_id
            WHERE a.person_id = ? AND (a.valid_to IS NULL OR a.valid_to >= ?)
            ORDER BY a.created_at DESC, a.id
            """,
            (person_id, as_of.isoformat()),
        ).fetchall()
        return [
            AffiliationRecord(
                affiliation=Affiliation(
                    id=row["id"],
                    person_id=row["person_id"],
                    org_id=row["org_id"],
                    role=row["role"],
                    period=_period(row),
                    confidence=row["confidence"],
                    provenance=_provenance(row),
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                organization_name=row["organization_name"],
            )
            for row in rows
        ]

    def list_facts(self, person_id: str) -> list[Fact]:
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE person_id = ? ORDER BY recorded_at DESC, id", (person_id,)
        ).fetchall()
        return [
            Fact(
                id=row["id"],
                person_id=row["person_id"],
                predicate=row["predicate"],
                value=row["value"],
                period=_period(row),
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                confidence=row["confidence"],
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
            )
            for row in rows
        ]

    def list_observations(self, person_id: str) -> list[Observation]:
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE person_id = ? ORDER BY observed_at DESC, id", (person_id,)
        ).fetchall()
        return [
            Observation(
                id=row["id"],
                person_id=row["person_id"],
                text=row["text"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
            )
            for row in rows
        ]

    def list_traits(self, person_id: str) -> list[Trait]:
        rows = self._conn.execute(
            "SELECT * FROM traits WHERE person_id = ? ORDER BY updated_at DESC, id", (person_id,)
        ).fetchall()
        return [
            Trait(
                id=row["id"],
                person_id=row["person_id"],
                category=TraitCategory(row["category"]),
                value=row["value"],
                evidence_note=row["evidence_note"],
                confidence=row["confidence"],
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def list_interactions(self, person_id: str) -> list[Interaction]:
        rows = self._conn.execute(
            """
            SELECT i.*
            FROM interactions i
            JOIN interaction_participants target
              ON target.interaction_id = i.id AND target.person_id = ?
            ORDER BY i.occurred_at DESC, i.id
            """,
            (person_id,),
        ).fetchall()
        return [
            Interaction(
                id=row["id"],
                summary=row["summary"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                channel=row["channel"],
                participant_ids=self._participant_ids(row["id"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                provenance=_provenance(row),
            )
            for row in rows
        ]

    def list_active_reminders(self, person_id: str) -> list[Reminder]:
        rows = self._conn.execute(
            """
            SELECT * FROM reminders
            WHERE person_id = ? AND status = 'active'
            ORDER BY created_at DESC, id
            """,
            (person_id,),
        ).fetchall()
        return [
            Reminder(
                id=row["id"],
                person_id=row["person_id"],
                text=row["text"],
                kind=ReminderKind(row["kind"]),
                due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
                recurrence=row["recurrence"],
                status=ReminderStatus(row["status"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def _participant_ids(self, interaction_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT person_id FROM interaction_participants WHERE interaction_id = ? ORDER BY person_id",
            (interaction_id,),
        ).fetchall()
        return [row["person_id"] for row in rows]


def _display_type(row: sqlite3.Row, person_id: str) -> str:
    if row["subject_id"] == person_id or row["relationship_symmetric"] or row["inverse_type"] is None:
        return row["type"]
    return row["inverse_type"]


def _period(row: sqlite3.Row) -> ValidityPeriod:
    return ValidityPeriod(
        valid_from=date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
        valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
    )


def _provenance(row: sqlite3.Row) -> Provenance:
    return Provenance(
        source=row["provenance_source"],
        session=row["provenance_session"],
        stated_by=row["provenance_stated_by"],
    )
