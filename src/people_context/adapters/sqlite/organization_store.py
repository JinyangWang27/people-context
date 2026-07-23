"""SQLite organization lookup and persistence."""

from __future__ import annotations

import sqlite3

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.organization import Organization
from people_context.domain.shared import normalize_name


class SqliteOrganizationStore:
    """SQLite organization lookup and persistence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, org_id: str) -> Organization | None:
        row = self._conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        return _organization(row) if row else None

    def get_by_normalized_name(self, normalized_name: str) -> Organization | None:
        row = self._conn.execute(
            "SELECT * FROM organizations WHERE name_normalized = ? ORDER BY id LIMIT 1",
            (normalized_name,),
        ).fetchone()
        return _organization(row) if row else None

    def save(self, organization: Organization) -> None:
        with SqliteUnitOfWork(self._conn):
            self._conn.execute(
                """INSERT INTO organizations (id, name, name_normalized, kind) VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name,
                       name_normalized = excluded.name_normalized,
                       kind = excluded.kind""",
                (organization.id, organization.name, normalize_name(organization.name), organization.kind),
            )


def _organization(row: sqlite3.Row) -> Organization:
    return Organization(id=row["id"], name=row["name"], kind=row["kind"])
