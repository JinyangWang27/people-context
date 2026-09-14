"""SQLite persistence and bounded disclosure-filtered reads for groups and memberships."""

from __future__ import annotations

import sqlite3
from typing import Any

from people_context.adapters.sqlite._projection import levels, placeholders
from people_context.adapters.sqlite.record_store import (
    group_membership_values,
    group_values,
    hydrate_group,
    hydrate_group_membership,
)
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.group import Group, GroupKind, GroupMembership
from people_context.domain.shared import Sensitivity, normalize_name

#: Membership order shared by both listings: known starts first and oldest first, then id.
_MEMBERSHIP_ORDER = "m.valid_from IS NULL, m.valid_from, m.id"


class SqliteGroupStore:
    """Groups and memberships on one connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save_group(self, group: Group) -> None:
        self._insert("identified_groups", group_values(group))

    def save_membership(self, membership: GroupMembership) -> None:
        self._insert("group_memberships", group_membership_values(membership))

    def find_groups(
        self,
        *,
        name: str | None,
        kind: GroupKind | None,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[Group]:
        bound = levels(sensitivities)
        params: dict[str, Any] = {**bound, "limit": limit + 1}
        clauses = [f"g.sensitivity IN ({placeholders(bound)})"]
        if name is not None:
            clauses.append("instr(g.name_normalized, :name) > 0")
            params["name"] = normalize_name(name)
        if kind is not None:
            clauses.append("g.kind = :kind")
            params["kind"] = kind.value
        rows = self._conn.execute(
            f"""SELECT g.* FROM identified_groups g WHERE {" AND ".join(clauses)}
                ORDER BY g.name_normalized, g.id LIMIT :limit""",  # noqa: S608 - fixed clauses, bound values
            params,
        ).fetchall()
        return [hydrate_group(row) for row in rows]

    def list_group_memberships(
        self,
        group_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[GroupMembership]:
        bound = levels(sensitivities)
        rows = self._conn.execute(
            f"""SELECT m.* FROM group_memberships m
                JOIN identified_groups g ON g.id = m.group_id
                JOIN persons p ON p.id = m.person_id
                WHERE m.group_id = :group_id AND p.deleted_at IS NULL
                  AND m.sensitivity IN ({placeholders(bound)}) AND g.sensitivity IN ({placeholders(bound)})
                ORDER BY {_MEMBERSHIP_ORDER} LIMIT :limit""",  # noqa: S608 - fixed clauses, bound values
            {**bound, "group_id": group_id, "limit": limit + 1},
        ).fetchall()
        return [hydrate_group_membership(row) for row in rows]

    def list_person_memberships(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[tuple[GroupMembership, Group]]:
        bound = levels(sensitivities)
        rows = self._conn.execute(
            f"""SELECT m.* FROM group_memberships m
                JOIN identified_groups g ON g.id = m.group_id
                WHERE m.person_id = :person_id
                  AND m.sensitivity IN ({placeholders(bound)}) AND g.sensitivity IN ({placeholders(bound)})
                ORDER BY {_MEMBERSHIP_ORDER} LIMIT :limit""",  # noqa: S608 - fixed clauses, bound values
            {**bound, "person_id": person_id, "limit": limit + 1},
        ).fetchall()
        memberships = [hydrate_group_membership(row) for row in rows]
        group_ids = sorted({membership.group_id for membership in memberships})
        groups = {
            row["id"]: hydrate_group(row)
            for row in self._conn.execute(
                f"SELECT * FROM identified_groups WHERE id IN ({', '.join('?' for _ in group_ids)})",  # noqa: S608
                group_ids,
            ).fetchall()
        }
        return [(membership, groups[membership.group_id]) for membership in memberships]

    def _insert(self, table: str, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with SqliteUnitOfWork(self._conn):
            self._conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})",  # noqa: S608 - internal table constants
                tuple(values.values()),
            )
