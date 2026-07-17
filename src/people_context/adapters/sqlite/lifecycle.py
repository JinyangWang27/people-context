"""SQLite adapter for atomic lifecycle mutations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditEntry
from people_context.ports.lifecycle import LifecycleTargetNotFoundError


class SqliteLifecycleStore:
    """Execute merge/forget multi-table operations on one SQLite transaction."""

    def __init__(self, conn: sqlite3.Connection, failure_hook: Callable[[str], None] | None = None) -> None:
        self._conn = conn
        self._failure_hook = failure_hook

    def merge_people(
        self,
        primary: Person,
        duplicate_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        """Re-parent duplicate-linked rows and append one audit atomically."""
        with self._conn:
            self._save_primary(primary, duplicate_id)
            counts = {
                "facts": self._reparent("facts", duplicate_id, primary.id),
                "observations": self._reparent("observations", duplicate_id, primary.id),
                "traits": self._reparent("traits", duplicate_id, primary.id),
                "reminders": self._reparent("reminders", duplicate_id, primary.id),
                "affiliations": self._reparent("affiliations", duplicate_id, primary.id),
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
            self._checkpoint("before_audit")
            self._insert_audit(audit_factory(counts))
        return counts

    def forget_person(
        self,
        person_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        """Hard-delete a person graph, redact prior audits, and add one tombstone."""
        with self._conn:
            counts = self.preview_person_forget(person_id)
            orphan_ids = [
                row["interaction_id"]
                for row in self._conn.execute(
                    """SELECT ip.interaction_id
                       FROM interaction_participants ip
                       WHERE ip.person_id = ?
                         AND (SELECT COUNT(*) FROM interaction_participants all_ip
                              WHERE all_ip.interaction_id = ip.interaction_id) = 1""",
                    (person_id,),
                ).fetchall()
            ]
            self._redact_audits(person_id, person_id)
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
            self._insert_audit(audit_factory(counts))
        return counts

    def forget_record(
        self,
        entity_type: str,
        entity_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        """Hard-delete one assertion/reminder/interaction and add one tombstone."""
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
        with self._conn:
            row = self._conn.execute(
                f"SELECT id FROM {table} WHERE id = ?",  # noqa: S608 - internal table map
                (entity_id,),
            ).fetchone()
            if row is None:
                raise LifecycleTargetNotFoundError(entity_id)
            deleted = {table: 1}
            if entity_type == "interaction":
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM interaction_participants WHERE interaction_id = ?",
                    (entity_id,),
                ).fetchone()[0]
                deleted["interaction_participations"] = count
            self._redact_audits(entity_id, None)
            self._conn.execute(
                f"DELETE FROM {table} WHERE id = ?",  # noqa: S608 - internal table map
                (entity_id,),
            )
            self._checkpoint("before_audit")
            self._insert_audit(audit_factory(deleted))
        return deleted

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

    def _insert_audit(self, entry: AuditEntry) -> None:
        self._conn.execute(
            """INSERT INTO audit_log (id, ts, op, entity_type, entity_id, payload_json, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.ts.isoformat(),
                entry.op,
                entry.entity_type,
                entry.entity_id,
                json.dumps(entry.payload),
                entry.source,
            ),
        )

    def _redact_audits(self, entity_id: str, forgotten_person_id: str | None) -> None:
        # Payload references can be nested at arbitrary depths. Decode every audit rather than using SQL LIKE,
        # which cannot distinguish exact scalar IDs and would miss JSON structure semantics.
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
