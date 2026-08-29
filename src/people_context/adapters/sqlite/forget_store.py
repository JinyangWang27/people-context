"""SQLite adapter for destructive forget operations and previews."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.evidence_cleanup import TraitEvidenceCleaner
from people_context.adapters.sqlite.import_cleanup import ImportCleanupResult, ImportProvenanceCleaner
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.lifecycle import (
    AffectedEntity,
    ForgetStoreResult,
    LifecycleTargetNotFoundError,
)


class SqliteForgetStore:
    """Delete person or record data and redact covered history atomically."""

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
        self._cleaner = ImportProvenanceCleaner(conn)
        self._evidence_cleaner = TraitEvidenceCleaner(conn)

    @property
    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a join-safe transaction boundary for application orchestration."""
        return SqliteUnitOfWork(self._conn)

    @property
    def audit_log(self) -> SqliteAuditLog:
        """Expose the paired mutation journal for app construction."""
        return SqliteAuditLog(
            self._conn,
            self._audit_failure_hook,
            changelog_failure_hook=self._changelog_failure_hook,
        )

    def forget_person(self, person_id: str) -> ForgetStoreResult:
        """Hard-delete a person graph and redact covered accountability and replay history."""
        with SqliteUnitOfWork(self._conn):
            counts = self.preview_person_forget(person_id)
            affected_entities, orphan_ids = self._person_forget_entities(person_id)
            targets = _entity_targets(affected_entities)
            # Evidence links are removed before the mappings and the records themselves, so the
            # ids they contribute reach the audit and changelog redaction below.
            evidence = self._evidence_cleaner.erase(targets)
            affected_entities.extend(evidence.affected_entities)
            counts.update(evidence.counts)
            cleanup = self._cleaner.erase(targets, person_id)
            affected_entities.extend(_mapping_entities(cleanup))
            counts.update(cleanup.counts)
            target_ids = {entity.entity_id for entity in affected_entities}
            target_ids.add(person_id)
            target_ids |= cleanup.redaction_targets
            self._redact_audits(target_ids, person_id)
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
            redacted_source_ids=cleanup.redacted_source_ids,
            deleted_source_ids=cleanup.deleted_source_ids,
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
            evidence = self._evidence_cleaner.erase([(entity_type, entity_id)])
            affected_entities.extend(evidence.affected_entities)
            deleted.update(evidence.counts)
            cleanup = self._cleaner.erase([(entity_type, entity_id)], None)
            affected_entities.extend(_mapping_entities(cleanup))
            deleted.update(cleanup.counts)
            target_ids = {entity.entity_id for entity in affected_entities}
            target_ids.add(entity_id)
            target_ids |= cleanup.redaction_targets
            self._redact_audits(target_ids, None)
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
            redacted_source_ids=cleanup.redacted_source_ids,
            deleted_source_ids=cleanup.deleted_source_ids,
        )

    def preview_person_forget(self, person_id: str) -> dict[str, int]:
        """Count rows a person-scope forget would delete without mutation.

        Import provenance and structurally linked staging rows are part of that count: a
        confirmation prompt that omitted them would understate what erasure removes, and a staged
        candidate holding the forgotten name is exactly what someone confirming this wants gone.
        """
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
        entities, _orphan_ids = self._person_forget_entities(person_id)
        targets = _entity_targets(entities)
        counts.update(self._evidence_cleaner.plan(targets).counts)
        counts.update(self._cleaner.plan(targets, person_id).counts)
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

    def _redact_audits(self, entity_ids: set[str], forgotten_person_id: str | None) -> None:
        """Redact accountability payloads covering anything this forget erased or scrubbed.

        The id set now includes candidate mappings and the receipts whose caller-authored label
        and external id were just cleared, because their audit payloads carry those values: a
        provenance row that survived here would let the erased record — or the wording that named
        it — be read back out of history.
        """
        rows = self._conn.execute("SELECT id, entity_id, payload_json FROM audit_log").fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            contains_person = forgotten_person_id is not None and _contains_scalar(payload, forgotten_person_id)
            if row["entity_id"] in entity_ids or contains_person:
                self._conn.execute(
                    "UPDATE audit_log SET payload_json = ? WHERE id = ?",
                    (json.dumps({"redacted": True}), row["id"]),
                )

    def _checkpoint(self, name: str) -> None:
        if self._failure_hook is not None:
            self._failure_hook(name)


def _entity_targets(entities: list[AffectedEntity]) -> list[tuple[str, str]]:
    """Return the (type, id) pairs a candidate mapping could legitimately point at."""
    return [(entity.entity_type, entity.entity_id) for entity in entities]


def _mapping_entities(cleanup: ImportCleanupResult) -> list[AffectedEntity]:
    """Return removed provenance as affected entities, so peer replay erases it too.

    Staging rows are deliberately absent: `import_staging` is operational state that peers never
    replicate, and its removal is reported through the deletion counts instead.
    """
    return [
        AffectedEntity(entity_type="import_candidate_mapping", entity_id=candidate_id)
        for candidate_id in cleanup.plan.mapping_candidate_ids
    ]


def _contains_scalar(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_scalar(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_scalar(item, target) for item in value)
    return value == target
