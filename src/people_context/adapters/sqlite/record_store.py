"""SQLite persistence for assertive records and reminders."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.records import Record

_TABLES = {
    "relationship": "relationships",
    "affiliation": "affiliations",
    "fact": "facts",
    "observation": "observations",
    "trait": "traits",
    "interaction": "interactions",
    "reminder": "reminders",
}

_UPDATE_COLUMNS = {
    "relationship": {"type", "label", "valid_from", "valid_to", "confidence"},
    "affiliation": {"role", "valid_from", "valid_to", "confidence"},
    "fact": {"predicate", "value", "valid_from", "valid_to", "confidence", "sensitivity"},
    "observation": {"text", "observed_at", "sensitivity"},
    "trait": {"category", "value", "evidence_note", "confidence", "sensitivity", "updated_at"},
    "interaction": {"summary", "occurred_at", "channel", "sensitivity"},
    "reminder": {"text", "kind", "due_at", "recurrence", "status"},
}


class SqliteRecordStore:
    """Read and write all non-person record types on one connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_relationship(self, relationship: Relationship) -> None:
        self._upsert(
            "relationships",
            {
                "id": relationship.id,
                "subject_id": relationship.subject_id,
                "object_id": relationship.object_id,
                "type": relationship.type,
                "label": relationship.label,
                **_period_values(relationship.period),
                "confidence": relationship.confidence,
                **_provenance_values(relationship.provenance),
                "created_at": relationship.created_at.isoformat(),
            },
        )

    def save_affiliation(self, affiliation: Affiliation) -> None:
        self._upsert(
            "affiliations",
            {
                "id": affiliation.id,
                "person_id": affiliation.person_id,
                "org_id": affiliation.org_id,
                "role": affiliation.role,
                **_period_values(affiliation.period),
                "confidence": affiliation.confidence,
                **_provenance_values(affiliation.provenance),
                "created_at": affiliation.created_at.isoformat(),
            },
        )

    def save_fact(self, fact: Fact) -> None:
        self._upsert(
            "facts",
            {
                "id": fact.id,
                "person_id": fact.person_id,
                "predicate": fact.predicate,
                "value": fact.value,
                **_period_values(fact.period),
                "recorded_at": fact.recorded_at.isoformat(),
                "confidence": fact.confidence,
                "sensitivity": fact.sensitivity.value,
                **_provenance_values(fact.provenance),
            },
        )

    def save_observation(self, observation: Observation) -> None:
        self._upsert(
            "observations",
            {
                "id": observation.id,
                "person_id": observation.person_id,
                "text": observation.text,
                "observed_at": observation.observed_at.isoformat(),
                "sensitivity": observation.sensitivity.value,
                **_provenance_values(observation.provenance),
            },
        )

    def save_trait(self, trait: Trait) -> None:
        self._upsert(
            "traits",
            {
                "id": trait.id,
                "person_id": trait.person_id,
                "category": trait.category.value,
                "value": trait.value,
                "evidence_note": trait.evidence_note,
                "confidence": trait.confidence,
                "sensitivity": trait.sensitivity.value,
                **_provenance_values(trait.provenance),
                "updated_at": trait.updated_at.isoformat(),
            },
        )

    def save_interaction(self, interaction: Interaction) -> None:
        values = {
            "id": interaction.id,
            "summary": interaction.summary,
            "occurred_at": interaction.occurred_at.isoformat(),
            "channel": interaction.channel,
            "sensitivity": interaction.sensitivity.value,
            **_provenance_values(interaction.provenance),
        }
        with SqliteUnitOfWork(self._conn):
            self._execute_upsert("interactions", values)
            self._conn.execute("DELETE FROM interaction_participants WHERE interaction_id = ?", (interaction.id,))
            self._conn.executemany(
                "INSERT INTO interaction_participants (interaction_id, person_id) VALUES (?, ?)",
                [(interaction.id, person_id) for person_id in dict.fromkeys(interaction.participant_ids)],
            )

    def save_reminder(self, reminder: Reminder) -> None:
        self._upsert(
            "reminders",
            {
                "id": reminder.id,
                "person_id": reminder.person_id,
                "text": reminder.text,
                "kind": reminder.kind.value,
                "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
                "recurrence": reminder.recurrence,
                "status": reminder.status.value,
                "created_at": reminder.created_at.isoformat(),
            },
        )

    def get_record(self, entity_type: str, entity_id: str) -> Record | None:
        table = _TABLES.get(entity_type)
        if table is None:
            return None
        row = self._conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        hydrator = _HYDRATORS[entity_type]
        return hydrator(row, self._participant_ids)

    def update_record_fields(self, entity_type: str, entity_id: str, fields: dict[str, Any]) -> Record | None:
        table = _TABLES.get(entity_type)
        allowed = _UPDATE_COLUMNS.get(entity_type)
        if table is None or allowed is None or not fields or not fields.keys() <= allowed:
            return None
        values = {key: _sqlite_value(value) for key, value in fields.items()}
        assignments = ", ".join(f"{column} = ?" for column in values)
        with SqliteUnitOfWork(self._conn):
            cursor = self._conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*values.values(), entity_id),
            )
        return self.get_record(entity_type, entity_id) if cursor.rowcount else None

    def list_reminders(
        self,
        person_id: str | None = None,
        due_before: datetime | None = None,
        status: ReminderStatus | None = ReminderStatus.ACTIVE,
    ) -> list[Reminder]:
        clauses: list[str] = []
        params: list[str] = []
        if person_id is not None:
            clauses.append("person_id = ?")
            params.append(person_id)
        if due_before is not None:
            clauses.append("((due_at IS NOT NULL AND due_at <= ?) OR (due_at IS NULL AND kind = 'communication_note'))")
            params.append(due_before.isoformat())
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT * FROM reminders {where}
                ORDER BY due_at IS NULL, due_at ASC, created_at DESC, id""",
            params,
        ).fetchall()
        return [_reminder(row) for row in rows]

    def _participant_ids(self, interaction_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT person_id FROM interaction_participants WHERE interaction_id = ? ORDER BY person_id",
            (interaction_id,),
        ).fetchall()
        return [row["person_id"] for row in rows]

    def _upsert(self, table: str, values: dict[str, Any]) -> None:
        with SqliteUnitOfWork(self._conn):
            self._execute_upsert(table, values)

    def _execute_upsert(self, table: str, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(f"{column} = excluded.{column}" for column in values if column != "id")
        self._conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}",
            tuple(values.values()),
        )


def _period_values(period: ValidityPeriod) -> dict[str, str | None]:
    return {
        "valid_from": period.valid_from.isoformat() if period.valid_from else None,
        "valid_to": period.valid_to.isoformat() if period.valid_to else None,
    }


def _provenance_values(provenance: Provenance) -> dict[str, str | None]:
    return {
        "provenance_source": provenance.source,
        "provenance_session": provenance.session,
        "provenance_stated_by": provenance.stated_by,
    }


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


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


def _relationship(row: sqlite3.Row, _: Callable[[str], list[str]]) -> Relationship:
    return Relationship(
        id=row["id"],
        subject_id=row["subject_id"],
        object_id=row["object_id"],
        type=row["type"],
        label=row["label"],
        period=_period(row),
        confidence=row["confidence"],
        provenance=_provenance(row),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _affiliation(row: sqlite3.Row, _: Callable[[str], list[str]]) -> Affiliation:
    return Affiliation(
        id=row["id"],
        person_id=row["person_id"],
        org_id=row["org_id"],
        role=row["role"],
        period=_period(row),
        confidence=row["confidence"],
        provenance=_provenance(row),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _fact(row: sqlite3.Row, _: Callable[[str], list[str]]) -> Fact:
    return Fact(
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


def _observation(row: sqlite3.Row, _: Callable[[str], list[str]]) -> Observation:
    return Observation(
        id=row["id"],
        person_id=row["person_id"],
        text=row["text"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        sensitivity=Sensitivity(row["sensitivity"]),
        provenance=_provenance(row),
    )


def _trait(row: sqlite3.Row, _: Callable[[str], list[str]]) -> Trait:
    return Trait(
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


def _interaction(row: sqlite3.Row, participants: Callable[[str], list[str]]) -> Interaction:
    return Interaction(
        id=row["id"],
        summary=row["summary"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        channel=row["channel"],
        participant_ids=participants(row["id"]),
        sensitivity=Sensitivity(row["sensitivity"]),
        provenance=_provenance(row),
    )


def _reminder(row: sqlite3.Row, _: Callable[[str], list[str]] | None = None) -> Reminder:
    return Reminder(
        id=row["id"],
        person_id=row["person_id"],
        text=row["text"],
        kind=ReminderKind(row["kind"]),
        due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
        recurrence=row["recurrence"],
        status=ReminderStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


_HYDRATORS: dict[str, Callable[[sqlite3.Row, Callable[[str], list[str]]], Record]] = {
    "relationship": _relationship,
    "affiliation": _affiliation,
    "fact": _fact,
    "observation": _observation,
    "trait": _trait,
    "interaction": _interaction,
    "reminder": _reminder,
}
