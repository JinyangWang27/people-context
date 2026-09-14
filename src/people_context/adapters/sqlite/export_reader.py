"""SQLite reader for complete portable exports."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.domain.organization import Organization
from people_context.domain.person import Person
from people_context.ports.audit_log import AuditEntry
from people_context.ports.export import ExportSnapshot
from people_context.ports.records import Record

_RECORD_TABLES = (
    ("affiliations", "affiliation"),
    ("relationships", "relationship"),
    ("facts", "fact"),
    ("observations", "observation"),
    ("traits", "trait"),
    ("interactions", "interaction"),
    ("reminders", "reminder"),
    ("identified_groups", "group"),
    ("group_memberships", "group_membership"),
)


class SqliteExportReader:
    """Hydrate every portable domain collection in deterministic order."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._people = SqlitePeopleRepository(conn)
        self._records = SqliteRecordStore(conn)

    def read_export(self) -> ExportSnapshot:
        """Return all domain rows while excluding FTS and import staging internals."""
        people = [
            self._person_payload(row["id"])
            for row in self._conn.execute("SELECT id FROM persons ORDER BY id").fetchall()
        ]
        organizations = [
            Organization(id=row["id"], name=row["name"], kind=row["kind"]).model_dump(mode="json")
            for row in self._conn.execute("SELECT * FROM organizations ORDER BY id").fetchall()
        ]
        records: dict[str, list[dict[str, Any]]] = {}
        for table, entity_type in _RECORD_TABLES:
            records[table] = [
                self._record_payload(entity_type, row["id"])
                for row in self._conn.execute(
                    f"SELECT id FROM {table} ORDER BY id"  # noqa: S608 - internal table constants
                ).fetchall()
            ]
        preferences = [
            {
                "key": row["key"],
                "value": json.loads(row["value_json"]),
                "updated_at": row["updated_at"],
            }
            for row in self._conn.execute("SELECT * FROM user_preferences ORDER BY key").fetchall()
        ]
        audit_log = [self._audit_entry(row).model_dump(mode="json") for row in self._audit_rows()]
        return ExportSnapshot(
            people=people,
            organizations=organizations,
            affiliations=records["affiliations"],
            relationships=records["relationships"],
            facts=records["facts"],
            observations=records["observations"],
            traits=records["traits"],
            interactions=records["interactions"],
            reminders=records["reminders"],
            user_preferences=preferences,
            audit_log=audit_log,
            groups=records["identified_groups"],
            group_memberships=records["group_memberships"],
        )

    def _person_payload(self, person_id: str) -> dict[str, Any]:
        person: Person | None = self._people.get(person_id)
        if person is None:
            raise RuntimeError(f"person disappeared during export: {person_id}")
        return person.model_dump(mode="json")

    def _record_payload(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        record: Record | None = self._records.get_record(entity_type, entity_id)
        if record is None:
            raise RuntimeError(f"{entity_type} disappeared during export: {entity_id}")
        return record.model_dump(mode="json")

    def _audit_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM audit_log ORDER BY ts, id").fetchall()

    @staticmethod
    def _audit_entry(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            id=row["id"],
            ts=datetime.fromisoformat(row["ts"]),
            op=row["op"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload_json"]),
            source=row["source"],
        )
