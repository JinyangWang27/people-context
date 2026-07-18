"""SQLite adapter for atomic lifecycle mutations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.lifecycle import (
    AffectedEntity,
    ForgetStoreResult,
    LifecycleChange,
    LifecycleTargetNotFoundError,
    MergeStoreResult,
)

_LINKED_TABLES = {
    "fact": "facts",
    "observation": "observations",
    "trait": "traits",
    "reminder": "reminders",
    "affiliation": "affiliations",
}


class SqliteLifecycleStore:
    """Execute merge/forget multi-table operations on one SQLite transaction."""

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
            relationships, self_loops = self._merge_relationships(primary.id, duplicate_id)
            counts["relationships"] = relationships
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
                "duplicate_tombstone": (people.get(duplicate_id) or primary).model_dump(mode="json"),
            }
            self._checkpoint("before_audit")
        return MergeStoreResult(counts=counts, changes=changes, manifest=manifest)

    def forget_person(self, person_id: str) -> ForgetStoreResult:
        """Hard-delete a person graph and redact covered accountability and replay history."""
        with SqliteUnitOfWork(self._conn):
            counts = self.preview_person_forget(person_id)
            affected_entities, orphan_ids = self._person_forget_entities(person_id)
            target_ids = {entity.entity_id for entity in affected_entities}
            target_ids.add(person_id)
            self._redact_audits(person_id, person_id)
            covered_ops, covered_transactions = SqliteChangelog(self._conn).redact_covered(target_ids)
            self._conn.execute("DELETE FROM person_search WHERE person_id = ?", (person_id,))
            self._conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
            if orphan_ids:
                placeholders = ", ".join("?" for _ in orphan_ids)
                self._conn.execute(
                    f"DELETE FROM interactions WHERE id IN ({placeholders})",  # noqa: S608 - placeholders only
                    orphan_ids,
                )
            counts["interactions"] = len(orphan_ids)
            self._checkpoint("before_audit")
        return ForgetStoreResult(
            deleted=counts,
            affected_entities=affected_entities,
            covered_op_ids=covered_ops,
            covered_transaction_ids=covered_transactions,
        )

    def forget_record(self, entity_type: str, entity_id: str) -> ForgetStoreResult:
        """Hard-delete one assertion/reminder/interaction and redact covered history."""
        tables = {
            "relationship": "relationships",
            "affiliation": "affiliations",
            "fact": "facts",
            "observation": "observations",
            "trait": "traits",
            "interaction": "interactions",
            "reminder": "reminders",
        }
        table = tables[entity_type]
        with SqliteUnitOfWork(self._conn):
            row = self._conn.execute(
                f"SELECT id FROM {table} WHERE id = ?",  # noqa: S608 - internal table map
                (entity_id,),
            ).fetchone()
            if row is None:
                raise LifecycleTargetNotFoundError(entity_id)
            affected_entities = [AffectedEntity(entity_type=entity_type, entity_id=entity_id)]
            deleted = {table: 1}
            if entity_type == "interaction":
                participant_rows = self._conn.execute(
                    "SELECT person_id FROM interaction_participants WHERE interaction_id = ? ORDER BY person_id",
                    (entity_id,),
                ).fetchall()
                deleted["interaction_participations"] = len(participant_rows)
                affected_entities.extend(
                    AffectedEntity(
                        entity_type="interaction_participant",
                        entity_id=f"{entity_id}:{participant['person_id']}",
                    )
                    for participant in participant_rows
                )
            target_ids = {entity.entity_id for entity in affected_entities}
            target_ids.add(entity_id)
            self._redact_audits(entity_id, None)
            covered_ops, covered_transactions = SqliteChangelog(self._conn).redact_covered(target_ids)
            self._conn.execute(
                f"DELETE FROM {table} WHERE id = ?",  # noqa: S608 - internal table map
                (entity_id,),
            )
            self._checkpoint("before_audit")
        return ForgetStoreResult(
            deleted=deleted,
            affected_entities=affected_entities,
            covered_op_ids=covered_ops,
            covered_transaction_ids=covered_transactions,
        )

    def preview_person_forget(self, person_id: str) -> dict[str, int]:
        """Count rows a person-scope forget would delete without mutation."""
        counts = {"persons": 1}
        for key, table in (
            ("aliases", "aliases"),
            ("facts", "facts"),
            ("observations", "observations"),
            ("traits", "traits"),
            ("reminders", "reminders"),
            ("affiliations", "affiliations"),
        ):
            counts[key] = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE person_id = ?",  # noqa: S608 - internal table constants
                (person_id,),
            ).fetchone()[0]
        counts["relationships"] = self._conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE subject_id = ? OR object_id = ?",
            (person_id, person_id),
        ).fetchone()[0]
        counts["interaction_participations"] = self._conn.execute(
            "SELECT COUNT(*) FROM interaction_participants WHERE person_id = ?",
            (person_id,),
        ).fetchone()[0]
        counts["interactions"] = self._conn.execute(
            """SELECT COUNT(*)
               FROM interaction_participants ip
               WHERE ip.person_id = ?
                 AND (SELECT COUNT(*) FROM interaction_participants all_ip
                      WHERE all_ip.interaction_id = ip.interaction_id) = 1""",
            (person_id,),
        ).fetchone()[0]
        return counts

    def _person_forget_entities(self, person_id: str) -> tuple[list[AffectedEntity], list[str]]:
        entities = [AffectedEntity(entity_type="person", entity_id=person_id)]
        for entity_type, table in (
            ("alias", "aliases"),
            ("fact", "facts"),
            ("observation", "observations"),
            ("trait", "traits"),
            ("reminder", "reminders"),
            ("affiliation", "affiliations"),
        ):
            entities.extend(
                AffectedEntity(entity_type=entity_type, entity_id=row["id"])
                for row in self._conn.execute(
                    f"SELECT id FROM {table} WHERE person_id = ? ORDER BY id",  # noqa: S608
                    (person_id,),
                ).fetchall()
            )
        entities.extend(
            AffectedEntity(entity_type="relationship", entity_id=row["id"])
            for row in self._conn.execute(
                "SELECT id FROM relationships WHERE subject_id = ? OR object_id = ? ORDER BY id",
                (person_id, person_id),
            ).fetchall()
        )
        participation_rows = self._conn.execute(
            "SELECT interaction_id FROM interaction_participants WHERE person_id = ? ORDER BY interaction_id",
            (person_id,),
        ).fetchall()
        entities.extend(
            AffectedEntity(
                entity_type="interaction_participant",
                entity_id=f"{row['interaction_id']}:{person_id}",
            )
            for row in participation_rows
        )
        orphan_ids = [
            row["interaction_id"]
            for row in self._conn.execute(
                """SELECT ip.interaction_id
                   FROM interaction_participants ip
                   WHERE ip.person_id = ?
                     AND (SELECT COUNT(*) FROM interaction_participants all_ip
                          WHERE all_ip.interaction_id = ip.interaction_id) = 1
                   ORDER BY ip.interaction_id""",
                (person_id,),
            ).fetchall()
        ]
        entities.extend(AffectedEntity(entity_type="interaction", entity_id=entity_id) for entity_id in orphan_ids)
        return entities, orphan_ids

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
        for row in relationship_rows:
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

    def _merge_relationships(self, primary_id: str, duplicate_id: str) -> tuple[int, int]:
        loop_cursor = self._conn.execute(
            """DELETE FROM relationships
               WHERE (subject_id = ? AND object_id IN (?, ?))
                  OR (object_id = ? AND subject_id IN (?, ?))""",
            (duplicate_id, primary_id, duplicate_id, duplicate_id, primary_id, duplicate_id),
        )
        rows = self._conn.execute(
            "SELECT id FROM relationships WHERE subject_id = ? OR object_id = ?",
            (duplicate_id, duplicate_id),
        ).fetchall()
        self._conn.execute("UPDATE relationships SET subject_id = ? WHERE subject_id = ?", (primary_id, duplicate_id))
        self._conn.execute("UPDATE relationships SET object_id = ? WHERE object_id = ?", (primary_id, duplicate_id))
        return len(rows), loop_cursor.rowcount

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

    def _redact_audits(self, entity_id: str, forgotten_person_id: str | None) -> None:
        rows = self._conn.execute("SELECT id, entity_id, payload_json FROM audit_log").fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            contains_person = forgotten_person_id is not None and _contains_scalar(payload, forgotten_person_id)
            if row["entity_id"] == entity_id or contains_person:
                self._conn.execute(
                    "UPDATE audit_log SET payload_json = ? WHERE id = ?",
                    (json.dumps({"redacted": True}), row["id"]),
                )

    def _checkpoint(self, name: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(name)


def _contains_scalar(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_scalar(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_scalar(item, target) for item in value)
    return value == target
