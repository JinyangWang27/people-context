"""Erasing import provenance and retained staging for entities hard forget actually removes.

Deleting a record is not enough on its own. Its provenance mapping would still name it, and an
incomplete import batch would still hold the staged candidate it came from — so the name a person
asked to have erased would remain reviewable through `pctx import review` after the record itself
was gone. This module removes both, inside the caller's forget transaction.

Two rules shape everything here.

**Structure, never text.** Dependent staging rows are found through the canonical typed reference
fields the stager wrote — `person_candidate_id`, `participant_candidate_ids`, the relationship end
points, evidence references — and never by scanning candidate text for a name. Guessing by name
would erase unrelated people who happen to share one and miss the ones spelled differently.

**Opaque metadata cannot be attributed.** A receipt label like `Interview with Alice` is the
caller's own wording about material that mentioned several people. There is no safe way to decide
it referred only to the person being forgotten, so any forget that touches a source clears that
source's caller-authored metadata outright — even when unrelated mappings on the same source
survive and stay perfectly usable.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.sources import STATUS_REDACTED

#: Canonical staging fields that name another candidate in the same batch. A dependent row is
#: found through exactly these; nothing here reads candidate prose.
_CANDIDATE_REFERENCE_FIELDS: tuple[str, ...] = (
    "person_candidate_id",
    "from_candidate_id",
    "to_candidate_id",
)

#: The same idea for fields holding a list of candidate ids.
_CANDIDATE_REFERENCE_LISTS: tuple[str, ...] = (
    "participant_candidate_ids",
    "evidence_candidate_ids",
)

#: Staging fields that name a durable record rather than a batch-local candidate.
_DURABLE_REFERENCE_LISTS: tuple[str, ...] = ("evidence_ids",)


@dataclass(frozen=True)
class ImportCleanupPlan:
    """Exactly what an import-provenance erasure would remove, computed without mutating."""

    mapping_candidate_ids: list[str] = field(default_factory=list)
    staging_ids: list[str] = field(default_factory=list)
    affected_source_ids: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Per-relation counts for forget previews and results.

        A relation this erasure does not touch is left out rather than reported as zero, so a
        database with no import provenance keeps the deletion summary it always had.
        """
        return {
            key: count
            for key, count in (
                ("candidate_mappings", len(self.mapping_candidate_ids)),
                ("import_staging", len(self.staging_ids)),
            )
            if count
        }


@dataclass(frozen=True)
class ImportCleanupResult:
    """What an import-provenance erasure removed, and which receipts it reduced."""

    plan: ImportCleanupPlan
    redacted_source_ids: list[str] = field(default_factory=list)
    deleted_source_ids: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Per-relation counts for forget results."""
        return self.plan.counts

    @property
    def redaction_targets(self) -> set[str]:
        """Ids whose audit and changelog history this forget must also redact.

        Mapping ids are provenance about an erased record, and receipt ids own history that
        carries the caller-authored metadata just scrubbed. Leaving either replayable would make
        import history a side channel around the erasure.
        """
        return {*self.plan.mapping_candidate_ids, *self.plan.affected_source_ids}


class ImportProvenanceCleaner:
    """Remove mappings, structurally linked staging, and emptied receipts for one forget."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def plan(self, entity_targets: Sequence[tuple[str, str]], person_id: str | None) -> ImportCleanupPlan:
        """Compute the whole erasure without touching a row.

        Preview and execution share this so the counts a confirmation prompt shows are the counts
        the deletion produces, including the leftovers of a batch whose every derived record this
        forget removes.
        """
        mapping_rows = self._mappings_for(entity_targets)
        mapping_candidate_ids = [row["candidate_id"] for row in mapping_rows]
        seeds = {
            *mapping_candidate_ids,
            *self._matched_person_staging_ids(person_id),
            *self._durable_evidence_staging_ids(entity_targets),
        }
        staging_ids = self._dependent_closure(seeds)
        sources = {row["source_session_id"] for row in mapping_rows}
        sources |= self._sources_of_staging(staging_ids)
        emptied = self._emptied_sources(sources, set(mapping_candidate_ids), staging_ids)
        for _source_id, batch_id, _digest in emptied:
            staging_ids |= self._staging_ids_of_batch(batch_id)
        return ImportCleanupPlan(
            mapping_candidate_ids=sorted(mapping_candidate_ids),
            staging_ids=sorted(staging_ids),
            affected_source_ids=sorted(sources),
        )

    def erase(self, entity_targets: Sequence[tuple[str, str]], person_id: str | None) -> ImportCleanupResult:
        """Delete the planned rows, scrub every affected receipt, and reduce emptied ones."""
        mapping_rows = self._mappings_for(entity_targets)
        mapping_candidate_ids = {row["candidate_id"] for row in mapping_rows}
        plan = self.plan(entity_targets, person_id)
        if not plan.affected_source_ids and not plan.mapping_candidate_ids and not plan.staging_ids:
            return ImportCleanupResult(plan=plan)
        emptied = self._emptied_sources(
            set(plan.affected_source_ids),
            mapping_candidate_ids,
            set(plan.staging_ids),
        )
        with SqliteUnitOfWork(self._conn):
            self._delete("import_candidate_mappings", "candidate_id", plan.mapping_candidate_ids)
            self._delete("import_staging", "id", plan.staging_ids)
            self._scrub_caller_metadata(plan.affected_source_ids)
            redacted, deleted = self._reduce(emptied)
        return ImportCleanupResult(plan=plan, redacted_source_ids=redacted, deleted_source_ids=deleted)

    # -- discovery -------------------------------------------------------

    def _mappings_for(self, entity_targets: Sequence[tuple[str, str]]) -> list[sqlite3.Row]:
        """Return live mappings pointing at any entity this forget actually erases.

        A terminal `merged_away` mapping has no entity id, so it cannot target an erased record
        and is never selected here: it is history about a candidate, not a reference to a row.
        """
        rows: list[sqlite3.Row] = []
        for entity_type, entity_id in entity_targets:
            rows.extend(
                self._conn.execute(
                    """SELECT candidate_id, batch_id, source_session_id
                       FROM import_candidate_mappings
                       WHERE disposition = 'entity' AND entity_type = ? AND entity_id = ?""",
                    (entity_type, entity_id),
                ).fetchall()
            )
        return rows

    def _matched_person_staging_ids(self, person_id: str | None) -> set[str]:
        """Return staged person candidates whose canonical match is the erased person."""
        if person_id is None:
            return set()
        return {
            row["id"]
            for row in self._staging_rows_mentioning(person_id)
            if json.loads(row["candidate_json"]).get("matched_person_id") == person_id
        }

    def _durable_evidence_staging_ids(self, entity_targets: Sequence[tuple[str, str]]) -> set[str]:
        """Return staged candidates citing an erased record as durable evidence."""
        erased = {entity_id for _entity_type, entity_id in entity_targets}
        matched: set[str] = set()
        for entity_id in erased:
            for row in self._staging_rows_mentioning(entity_id):
                candidate = json.loads(row["candidate_json"])
                if any(
                    entity_id in _as_ids(candidate.get(field_name))
                    for field_name in _DURABLE_REFERENCE_LISTS
                ):
                    matched.add(row["id"])
        return matched

    def _staging_rows_mentioning(self, value: str) -> list[sqlite3.Row]:
        """Narrow the scan to rows whose stored JSON could contain one id, then decide exactly.

        The `LIKE` is only a filter: every candidate it returns is parsed and checked against the
        canonical field it must appear in, so a coincidental substring never deletes a row.
        """
        return self._conn.execute(
            "SELECT id, candidate_json FROM import_staging WHERE candidate_json LIKE ?",
            (f"%{value}%",),
        ).fetchall()

    def _dependent_closure(self, seeds: set[str]) -> set[str]:
        """Grow the seed set until no remaining staged candidate references a removed one.

        Candidate references are batch-local by construction, so only the batches the seeds
        belong to can contain dependents. Reading just those batches keeps the fixed point cheap
        on a database with unrelated import history.
        """
        if not seeds:
            return set()
        removed = set(seeds)
        rows = self._rows_of_batches(self._batches_of(removed))
        changed = True
        while changed:
            changed = False
            for row_id, candidate in rows.items():
                if row_id in removed:
                    continue
                if _references(candidate) & removed:
                    removed.add(row_id)
                    changed = True
        return removed

    def _batches_of(self, staging_ids: set[str]) -> set[str]:
        batches: set[str] = set()
        for chunk in _chunks(sorted(staging_ids)):
            placeholders = ", ".join("?" for _ in chunk)
            batches.update(
                row["batch_id"]
                for row in self._conn.execute(
                    f"SELECT DISTINCT batch_id FROM import_staging WHERE id IN ({placeholders})",  # noqa: S608
                    chunk,
                ).fetchall()
            )
        return batches

    def _rows_of_batches(self, batch_ids: set[str]) -> dict[str, dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        for batch_id in sorted(batch_ids):
            for row in self._conn.execute(
                "SELECT id, candidate_json FROM import_staging WHERE batch_id = ?",
                (batch_id,),
            ).fetchall():
                rows[row["id"]] = json.loads(row["candidate_json"])
        return rows

    def _sources_of_staging(self, staging_ids: set[str]) -> set[str]:
        if not staging_ids:
            return set()
        sources: set[str] = set()
        for batch_id in sorted(self._batches_of(staging_ids)):
            sources.update(
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM import_source_sessions WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
            )
        return sources

    # -- mutation --------------------------------------------------------

    def _delete(self, table: str, key: str, ids: Sequence[str]) -> None:
        for chunk in _chunks(list(ids)):
            placeholders = ", ".join("?" for _ in chunk)
            self._conn.execute(
                f"DELETE FROM {table} WHERE {key} IN ({placeholders})",  # noqa: S608 - internal constants
                chunk,
            )

    def _scrub_caller_metadata(self, source_ids: Sequence[str]) -> None:
        """Clear caller-authored receipt fields on every source this forget touched."""
        self._conn.executemany(
            "UPDATE import_source_sessions SET label = NULL, external_source_id = NULL WHERE id = ?",
            [(source_id,) for source_id in source_ids],
        )

    def _emptied_sources(
        self,
        source_ids: set[str],
        removed_mapping_ids: set[str],
        removed_staging_ids: set[str],
    ) -> list[tuple[str, str | None, str | None]]:
        """Return the affected sources that this erasure leaves with nothing live behind.

        "Nothing live" is no surviving mapping and no surviving reviewable staging row. A source
        that still provenances someone else's records stays inspectable — with its caller-authored
        metadata scrubbed — because the erasure took only part of what it produced.
        """
        emptied: list[tuple[str, str | None, str | None]] = []
        for source_id in sorted(source_ids):
            row = self._conn.execute(
                "SELECT id, claim_key, batch_id, status FROM import_source_sessions WHERE id = ?",
                (source_id,),
            ).fetchone()
            if row is None or row["status"] == STATUS_REDACTED:
                continue
            if self._surviving_mappings(source_id, removed_mapping_ids):
                continue
            if self._surviving_reviewable_staging(row["batch_id"], removed_staging_ids):
                continue
            emptied.append((source_id, row["batch_id"], row["claim_key"]))
        return emptied

    def _reduce(self, emptied: list[tuple[str, str | None, str | None]]) -> tuple[list[str], list[str]]:
        """Reduce each emptied source to its claim, or remove it entirely.

        A receipt that owns a canonical claim keeps only what makes that claim non-restageable:
        its internal id, the non-personal machine kind, the digest, the fingerprint state, and the
        terminal status.

        Retention is decided by the claim rather than by the digest, because the claim is the only
        thing retention is *for*. A digestless session never had one. Neither did an explicit
        `--force` reprocessing session: it carries a digest but competes for no canonical key, so
        duplicate detection would never find it again. Keeping it would leave the digest of an
        erased artifact in the database — and in every later bundle — while suppressing nothing.
        """
        redacted: list[str] = []
        deleted: list[str] = []
        for source_id, _batch_id, claim_key in emptied:
            if claim_key is None:
                self._conn.execute("DELETE FROM import_source_sessions WHERE id = ?", (source_id,))
                deleted.append(source_id)
                continue
            self._conn.execute(
                """UPDATE import_source_sessions
                   SET status = ?, batch_id = NULL, label = NULL, external_source_id = NULL,
                       extraction_contract_revision = NULL
                   WHERE id = ?""",
                (STATUS_REDACTED, source_id),
            )
            redacted.append(source_id)
        return redacted, deleted

    def _surviving_mappings(self, source_id: str, removed_mapping_ids: set[str]) -> bool:
        return any(
            row["candidate_id"] not in removed_mapping_ids
            for row in self._conn.execute(
                "SELECT candidate_id FROM import_candidate_mappings WHERE source_session_id = ?",
                (source_id,),
            ).fetchall()
        )

    def _surviving_reviewable_staging(self, batch_id: str | None, removed_staging_ids: set[str]) -> bool:
        if batch_id is None:
            return False
        return any(
            row["id"] not in removed_staging_ids
            for row in self._conn.execute(
                "SELECT id FROM import_staging WHERE batch_id = ? AND status <> 'committed'",
                (batch_id,),
            ).fetchall()
        )

    def _staging_ids_of_batch(self, batch_id: str | None) -> set[str]:
        """Return every staging row of a batch whose derived records are all gone."""
        if batch_id is None:
            return set()
        return {
            row["id"]
            for row in self._conn.execute(
                "SELECT id FROM import_staging WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
        }


def _references(candidate: dict[str, object]) -> set[str]:
    """Return every batch-local candidate id one staged candidate canonically names."""
    references = {
        value
        for field_name in _CANDIDATE_REFERENCE_FIELDS
        if isinstance(value := candidate.get(field_name), str)
    }
    for field_name in _CANDIDATE_REFERENCE_LISTS:
        references |= _as_ids(candidate.get(field_name))
    return references


def _as_ids(value: object) -> set[str]:
    """Return the identifier strings of a canonical reference list, ignoring anything else."""
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    """Split ids into bound-parameter-sized batches, because SQLite caps variables per statement."""
    for start in range(0, len(values), size):
        yield values[start : start + size]
