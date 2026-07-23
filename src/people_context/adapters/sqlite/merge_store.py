"""SQLite adapter for atomic person merges."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.person import Person
from people_context.domain.shared import ValidityPeriod, normalize_name
from people_context.ports.lifecycle import (
    LifecycleChange,
    MergeStoreResult,
)

_LINKED_TABLES = {
    "fact": "facts",
    "observation": "observations",
    "trait": "traits",
    "reminder": "reminders",
    "affiliation": "affiliations",
}


class SqliteMergeStore:
    """Execute multi-table person merges on one SQLite transaction."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        failure_hook: Callable[[str], None] | None = None,
        *,
        audit_failure_hook: Callable[[str], None] | None = None,
        changelog_failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._conn = conn
        self._failure_hook = failure_hook
        self._audit_failure_hook = audit_failure_hook
        self._changelog_failure_hook = changelog_failure_hook

    @property
    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a join-safe transaction boundary for application orchestration."""
        return SqliteUnitOfWork(self._conn)

    @property
    def audit_log(self) -> SqliteAuditLog:
        """Expose the paired mutation journal for backward-compatible app construction."""
        return SqliteAuditLog(
            self._conn,
            self._audit_failure_hook,
            changelog_failure_hook=self._changelog_failure_hook,
        )

    def merge_people(self, primary: Person, duplicate_id: str) -> MergeStoreResult:
        """Re-parent duplicate-linked rows and return exact row outcomes."""
        with SqliteUnitOfWork(self._conn):
            people = SqlitePeopleRepository(self._conn)
            records = SqliteRecordStore(self._conn)
            primary_before = people.get(primary.id)
            linked_ids = {
                entity_type: self._ids_for_person(table, duplicate_id) for entity_type, table in _LINKED_TABLES.items()
            }
            relationship_rows = self._conn.execute(
                "SELECT id, subject_id, object_id FROM relationships WHERE subject_id = ? OR object_id = ?",
                (duplicate_id, duplicate_id),
            ).fetchall()
            participation_ids = [
                row["interaction_id"]
                for row in self._conn.execute(
                    "SELECT interaction_id FROM interaction_participants WHERE person_id = ? ORDER BY interaction_id",
                    (duplicate_id,),
                ).fetchall()
            ]

            self._save_primary(primary, duplicate_id)
            counts = {
                f"{entity_type}s" if entity_type != "affiliation" else "affiliations": self._reparent(
                    table, duplicate_id, primary.id
                )
                for entity_type, table in _LINKED_TABLES.items()
            }
            moved_edge_ids, self_loops = self._merge_relationships(primary.id, duplicate_id)
            counts["relationships"] = len(moved_edge_ids)
            self._recanonicalize_symmetric(moved_edge_ids)
            deduped = self._dedupe_relationships(primary.id, primary.updated_at.date(), moved_edge_ids)
            counts["duplicate_relationships_removed"] = len(deduped)
            counts["interaction_participations"] = self._merge_participations(primary.id, duplicate_id)
            counts["self_loops_removed"] = self_loops
            self._conn.execute(
                "UPDATE persons SET is_self = 0, deleted_at = ?, updated_at = ? WHERE id = ?",
                (primary.updated_at.isoformat(), primary.updated_at.isoformat(), duplicate_id),
            )
            self._conn.execute("DELETE FROM person_search WHERE person_id = ?", (duplicate_id,))

            changes = self._merge_changes(
                primary,
                primary_before,
                duplicate_id,
                linked_ids,
                relationship_rows,
                participation_ids,
                people,
                records,
                deduped,
            )
            manifest = {
                "primary_id": primary.id,
                "duplicate_id": duplicate_id,
                "final_primary": primary.model_dump(mode="json"),
                "affected_rows": [
                    {
                        "entity_type": change.entity_type,
                        "entity_id": change.entity_id,
                        "op_kind": change.op_kind,
                        "changed_fields": change.changed_fields,
                    }
                    for change in changes
                ],
                "removed_relationship_ids": [
                    row["id"] for row in relationship_rows if records.get_record("relationship", row["id"]) is None
                ],
                "deduped_relationships": [
                    {"id": removed_id, "merged_into": keeper_id} for removed_id, keeper_id in deduped
                ],
                "duplicate_tombstone": (people.get(duplicate_id) or primary).model_dump(mode="json"),
            }
            self._checkpoint("before_audit")
        return MergeStoreResult(counts=counts, changes=changes, manifest=manifest)

    def _merge_changes(
        self,
        primary: Person,
        primary_before: Person | None,
        duplicate_id: str,
        linked_ids: dict[str, list[str]],
        relationship_rows: list[sqlite3.Row],
        participation_ids: list[str],
        people: SqlitePeopleRepository,
        records: SqliteRecordStore,
        deduped: list[tuple[str, str]],
    ) -> list[LifecycleChange]:
        changes: list[LifecycleChange] = []
        primary_payload = primary.model_dump(mode="json")
        before_payload = primary_before.model_dump(mode="json") if primary_before is not None else {}
        changes.append(
            LifecycleChange(
                entity_type="person",
                entity_id=primary.id,
                op_kind="update",
                payload=primary_payload,
                changed_fields=sorted(
                    key for key in primary_payload if primary_payload.get(key) != before_payload.get(key)
                ),
            )
        )
        duplicate = people.get(duplicate_id)
        if duplicate is not None:
            changes.append(
                LifecycleChange(
                    entity_type="person",
                    entity_id=duplicate_id,
                    op_kind="update",
                    payload=duplicate.model_dump(mode="json"),
                    changed_fields=["deleted_at", "is_self", "updated_at"],
                )
            )
        for entity_type, entity_ids in linked_ids.items():
            for entity_id in entity_ids:
                record = records.get_record(entity_type, entity_id)
                if record is not None:
                    changes.append(
                        LifecycleChange(
                            entity_type=entity_type,
                            entity_id=entity_id,
                            op_kind="update",
                            payload=record.model_dump(mode="json"),
                            changed_fields=["person_id"],
                        )
                    )
        deduped_keepers = {removed_id: keeper_id for removed_id, keeper_id in deduped}
        for removed_id, keeper_id in deduped:
            changes.append(
                LifecycleChange(
                    entity_type="relationship",
                    entity_id=removed_id,
                    op_kind="delete",
                    payload={"id": removed_id, "deleted": True, "merged_into": keeper_id},
                    changed_fields=["deleted"],
                )
            )
        for row in relationship_rows:
            if row["id"] in deduped_keepers:
                continue
            record = records.get_record("relationship", row["id"])
            if record is None:
                changes.append(
                    LifecycleChange(
                        entity_type="relationship",
                        entity_id=row["id"],
                        op_kind="merge",
                        payload={"id": row["id"], "deleted": True},
                    )
                )
                continue
            changed_fields = []
            if record.subject_id != row["subject_id"]:
                changed_fields.append("subject_id")
            if record.object_id != row["object_id"]:
                changed_fields.append("object_id")
            changes.append(
                LifecycleChange(
                    entity_type="relationship",
                    entity_id=row["id"],
                    op_kind="update",
                    payload=record.model_dump(mode="json"),
                    changed_fields=changed_fields,
                )
            )
        for interaction_id in participation_ids:
            changes.append(
                LifecycleChange(
                    entity_type="interaction_participant",
                    entity_id=f"{interaction_id}:{primary.id}",
                    op_kind="update",
                    payload={"interaction_id": interaction_id, "person_id": primary.id},
                    changed_fields=["person_id"],
                )
            )
        return changes

    def _save_primary(self, person: Person, duplicate_id: str) -> None:
        self._conn.execute(
            """UPDATE persons SET canonical_name = ?, canonical_name_normalized = ?, is_self = ?,
               summary = ?, updated_at = ?, deleted_at = NULL WHERE id = ?""",
            (
                person.canonical_name,
                normalize_name(person.canonical_name),
                int(person.is_self),
                person.summary,
                person.updated_at.isoformat(),
                person.id,
            ),
        )
        self._conn.execute("DELETE FROM aliases WHERE person_id IN (?, ?)", (person.id, duplicate_id))
        self._conn.executemany(
            """INSERT INTO aliases (id, person_id, value, value_normalized, kind, lang, script)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    alias.id,
                    person.id,
                    alias.value,
                    normalize_name(alias.value),
                    alias.kind.value,
                    alias.lang,
                    alias.script,
                )
                for alias in person.aliases
            ],
        )
        self._conn.execute("DELETE FROM person_search WHERE person_id = ?", (person.id,))
        self._conn.executemany(
            "INSERT INTO person_search (name, person_id) VALUES (?, ?)",
            [(name, person.id) for name in person.all_names()],
        )

    def _ids_for_person(self, table: str, person_id: str) -> list[str]:
        return [
            row["id"]
            for row in self._conn.execute(
                f"SELECT id FROM {table} WHERE person_id = ? ORDER BY id",  # noqa: S608
                (person_id,),
            ).fetchall()
        ]

    def _reparent(self, table: str, duplicate_id: str, primary_id: str) -> int:
        cursor = self._conn.execute(
            f"UPDATE {table} SET person_id = ? WHERE person_id = ?",  # noqa: S608 - table is internal constant
            (primary_id, duplicate_id),
        )
        return cursor.rowcount

    def _recanonicalize_symmetric(self, moved_edge_ids: list[str]) -> None:
        """Restore ID-ordered endpoints on re-parented symmetric edges.

        Raw re-parenting can leave a symmetric edge with subject > object, which
        the exact-direction active-edge lookup would then miss, letting a later
        assertion insert a parallel duplicate.
        """
        if not moved_edge_ids:
            return
        placeholders = ", ".join("?" for _ in moved_edge_ids)
        rows = self._conn.execute(
            f"""SELECT r.id, r.subject_id, r.object_id
                FROM relationships r
                JOIN relationship_types rt ON rt.type = r.type AND rt.symmetric = 1
                WHERE r.id IN ({placeholders}) AND r.subject_id > r.object_id""",  # noqa: S608 - bound placeholders
            moved_edge_ids,
        ).fetchall()
        self._conn.executemany(
            "UPDATE relationships SET subject_id = ?, object_id = ? WHERE id = ?",
            [(row["object_id"], row["subject_id"], row["id"]) for row in rows],
        )

    def _dedupe_relationships(self, primary_id: str, as_of: date, moved_edge_ids: list[str]) -> list[tuple[str, str]]:
        """Remove same-type overlapping-period edges left parallel by re-parenting.

        Mirrors the normalize-relationships policy: the kept edge is the active one
        as of the merge, then the oldest; symmetric types match either direction.
        Only collisions involving an edge moved from the duplicate are collapsed —
        pre-existing parallel edges between the primary and a third person are the
        business of normalize-relationships, not merge. Disjoint-period history is
        never collapsed.
        """
        moved = set(moved_edge_ids)
        if not moved:
            return []
        symmetric_types = {
            row["type"]
            for row in self._conn.execute("SELECT type FROM relationship_types WHERE symmetric = 1").fetchall()
        }
        rows = self._conn.execute(
            """SELECT id, subject_id, object_id, type, valid_from, valid_to, created_at
               FROM relationships WHERE subject_id = ? OR object_id = ?""",
            (primary_id, primary_id),
        ).fetchall()

        def period(row: sqlite3.Row) -> ValidityPeriod:
            return ValidityPeriod(
                valid_from=date.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
                valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            )

        ordered = sorted(rows, key=lambda row: (not period(row).contains(as_of), row["created_at"], row["id"]))
        keepers: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        removed: list[tuple[str, str]] = []
        for row in ordered:
            endpoints = (row["subject_id"], row["object_id"])
            if row["type"] in symmetric_types:
                endpoints = tuple(sorted(endpoints))
            group = keepers.setdefault((row["type"], *endpoints), [])
            keeper = next((candidate for candidate in group if period(candidate).overlaps(period(row))), None)
            if keeper is None or (row["id"] not in moved and keeper["id"] not in moved):
                group.append(row)
                continue
            removed.append((row["id"], keeper["id"]))
        if removed:
            self._conn.executemany(
                "DELETE FROM relationships WHERE id = ?",
                [(removed_id,) for removed_id, _ in removed],
            )
        return removed

    def _merge_relationships(self, primary_id: str, duplicate_id: str) -> tuple[list[str], int]:
        loop_cursor = self._conn.execute(
            """DELETE FROM relationships
               WHERE (subject_id = ? AND object_id IN (?, ?))
                  OR (object_id = ? AND subject_id IN (?, ?))""",
            (duplicate_id, primary_id, duplicate_id, duplicate_id, primary_id, duplicate_id),
        )
        rows = self._conn.execute(
            "SELECT id FROM relationships WHERE subject_id = ? OR object_id = ? ORDER BY id",
            (duplicate_id, duplicate_id),
        ).fetchall()
        self._conn.execute("UPDATE relationships SET subject_id = ? WHERE subject_id = ?", (primary_id, duplicate_id))
        self._conn.execute("UPDATE relationships SET object_id = ? WHERE object_id = ?", (primary_id, duplicate_id))
        return [row["id"] for row in rows], loop_cursor.rowcount

    def _merge_participations(self, primary_id: str, duplicate_id: str) -> int:
        rows = self._conn.execute(
            "SELECT interaction_id FROM interaction_participants WHERE person_id = ?",
            (duplicate_id,),
        ).fetchall()
        for row in rows:
            collision = self._conn.execute(
                "SELECT 1 FROM interaction_participants WHERE interaction_id = ? AND person_id = ?",
                (row["interaction_id"], primary_id),
            ).fetchone()
            if collision:
                self._conn.execute(
                    "DELETE FROM interaction_participants WHERE interaction_id = ? AND person_id = ?",
                    (row["interaction_id"], duplicate_id),
                )
            else:
                self._conn.execute(
                    "UPDATE interaction_participants SET person_id = ? WHERE interaction_id = ? AND person_id = ?",
                    (primary_id, row["interaction_id"], duplicate_id),
                )
        return len(rows)

    def _checkpoint(self, name: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(name)

