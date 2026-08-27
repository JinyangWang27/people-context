"""SQLite verbatim bootstrap restore into a baseline-empty destination.

Every ordinary durable mutation flows through the application ``audit_mutation`` seam. This
writer is the one documented exception: it reinstates the origin device's rows exactly as
they were recorded, including their audit and changelog history, so minting fresh
accountability rows here would fabricate history rather than preserve it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from typing import Any

from people_context.domain.relationship_vocabulary import (
    SEEDED_RELATIONSHIP_SYNONYMS,
    SEEDED_RELATIONSHIP_TYPES,
)
from people_context.domain.shared import normalize_name
from people_context.domain.sync_bundle import (
    BundleDevice,
    BundleProvenance,
    BundleValidityPeriod,
    InvalidBundleError,
    RestoreUnavailableError,
    SyncBundleDocument,
    TargetNotEmptyError,
)
from people_context.ports.bootstrap_restore import RestoreOutcome
from people_context.ports.hlc import HlcTimestamp, HybridLogicalClock
from people_context.ports.repository import PersonSearchIndexer

_VECTOR_TABLE = "semantic_vectors"
_TYPE_COLUMNS_SQL = "SELECT type, inverse, symmetric, category, canonical FROM relationship_types"
_SYNONYM_COLUMNS_SQL = "SELECT synonym, type FROM relationship_type_synonyms"

# "Fresh" is not "no person rows": staging, preferences, audit, sync, and derived search rows
# can all exist without one, and this writer neither owns nor merges them.
_BASELINE_EMPTY_TABLES = (
    "persons",
    "aliases",
    "organizations",
    "affiliations",
    "relationships",
    "facts",
    "observations",
    "traits",
    "interactions",
    "interaction_participants",
    "reminders",
    "user_preferences",
    "import_staging",
    "import_source_sessions",
    "import_candidate_mappings",
    "audit_log",
    "changelog",
    "sync_conflicts",
    "person_search",
)


class SqliteBootstrapRestorer:
    """Write one validated bundle verbatim under a single ``BEGIN IMMEDIATE`` reservation.

    The reservation is taken before the baseline-empty checks so that no concurrent writer
    can slip a row in between validating the destination and filling it.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        indexer: PersonSearchIndexer,
        hybrid_clock: HybridLogicalClock,
        *,
        phase_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._conn = conn
        self._indexer = indexer
        self._hybrid_clock = hybrid_clock
        self._phase_hook = phase_hook

    def restore(self, document: SyncBundleDocument) -> RestoreOutcome:
        """Restore the document, or raise a structured refusal having written nothing."""
        self._reserve()
        try:
            self._hook("reservation")
            local_device_id = self._require_baseline_target()
            self._hook("baseline")
            self._reject_device_collision(document, local_device_id)
            types, synonyms = self._reconcile_vocabulary(document)
            self._hook("vocabulary")
            self._insert_devices(document)
            self._hook("devices")
            self._insert_domain(document)
            self._hook("domain")
            self._insert_imports(document)
            self._hook("imports")
            self._insert_changelog(document)
            self._hook("changelog")
            _, indexed_names = self._indexer.rebuild_person_search()
            self._hook("fts")
            watermark = self._hybrid_clock.observe(
                HlcTimestamp(document.watermark.hlc_physical_ms, document.watermark.hlc_logical)
            )
            self._hook("hlc")
        except BaseException:
            self._conn.rollback()
            raise
        self._conn.commit()
        return _outcome(document, types, synonyms, indexed_names, watermark)

    # -- reservation and baseline ----------------------------------------

    def _reserve(self) -> None:
        if self._conn.in_transaction:
            raise RestoreUnavailableError(["restore must own its transaction; an outer transaction is open"])
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        # DB-API exposes the driver's own exception classes on the connection, so
        # this stays correct for both the stdlib and the SQLCipher binding.
        except self._conn.OperationalError as exc:
            raise RestoreUnavailableError([f"cannot reserve the destination for writing: {exc}"]) from exc

    def _hook(self, phase: str) -> None:
        if self._phase_hook is not None:
            self._phase_hook(phase)

    def _require_baseline_target(self) -> str:
        """Return the destination's own device id, or refuse without writing anything."""
        details = [
            f"{table}: {count} row(s)"
            for table, count in ((table, self._count(table)) for table in _BASELINE_EMPTY_TABLES)
            if count
        ]
        devices = self._conn.execute("SELECT id, retired_at FROM devices ORDER BY created_at, id").fetchall()
        active = [row["id"] for row in devices if row["retired_at"] is None]
        if len(devices) != 1 or len(active) != 1:
            details.append(f"devices: {len(devices)} row(s) of which {len(active)} active, expected exactly 1 active")
        details.extend(self._vocabulary_drift_details())
        details.extend(self._semantic_vector_details())
        if details:
            raise TargetNotEmptyError(details)
        return str(active[0])

    def _count(self, table: str) -> int:
        row = self._conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()  # noqa: S608 - fixed constants
        return int(row["total"])

    def _vocabulary_drift_details(self) -> list[str]:
        """Report drift as counts only: custom type labels are the user's own wording."""
        types = {
            row["type"]: (row["inverse"], bool(row["symmetric"]), row["category"], bool(row["canonical"]))
            for row in self._conn.execute(_TYPE_COLUMNS_SQL)
        }
        seeded_types = {
            key: (row.inverse, row.symmetric, row.category, row.canonical)
            for key, row in SEEDED_RELATIONSHIP_TYPES.items()
        }
        synonyms = {
            row["synonym"]: row["type"]
            for row in self._conn.execute(_SYNONYM_COLUMNS_SQL)
        }
        details: list[str] = []
        drifted_types = _difference_count(types, seeded_types)
        if drifted_types:
            details.append(f"relationship_types: {drifted_types} custom, missing, or drifted row(s)")
        drifted_synonyms = _difference_count(synonyms, dict(SEEDED_RELATIONSHIP_SYNONYMS))
        if drifted_synonyms:
            details.append(f"relationship_type_synonyms: {drifted_synonyms} custom, missing, or drifted row(s)")
        return details

    def _semantic_vector_details(self) -> list[str]:
        """Treat optional vector storage as occupied whenever it exists and is non-empty.

        A freshly initialized database has no vector table at all, so its mere presence
        already means something ran against this destination. When the table exists but the
        extension that defines it is not loaded on this connection, refuse rather than
        assume it is empty.
        """
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_VECTOR_TABLE,),
        ).fetchone()
        if exists is None:
            return []
        try:
            count = self._count(_VECTOR_TABLE)
        except self._conn.OperationalError:
            return [f"{_VECTOR_TABLE}: present but unreadable on this connection"]
        return [f"{_VECTOR_TABLE}: {count} row(s)"] if count else []

    def _reject_device_collision(self, document: SyncBundleDocument, local_device_id: str) -> None:
        """Never retire or overwrite the destination's own identity."""
        if any(device.id == local_device_id for device in document.devices):
            raise InvalidBundleError(
                [f"bundle carries the destination's own active device id: {local_device_id}"]
            )

    # -- writes ----------------------------------------------------------

    def _reconcile_vocabulary(self, document: SyncBundleDocument) -> tuple[int, int]:
        """Insert new vocabulary, skip identical seeded rows, and reject a differing row.

        Reconciliation must never change how a name already resolves. ``resolve()`` prefers an
        exact type over a synonym, so a bundled type sharing a destination synonym's name — or
        a bundled synonym already claimed by a known type — would silently redirect every later
        assertion using that name instead of failing loudly.
        """
        vocabulary = document.relationship_vocabulary
        existing_types = {
            row["type"]: (row["inverse"], bool(row["symmetric"]), row["category"], bool(row["canonical"]))
            for row in self._conn.execute(_TYPE_COLUMNS_SQL)
        }
        existing_synonyms = {row["synonym"]: row["type"] for row in self._conn.execute(_SYNONYM_COLUMNS_SQL)}

        inserted_types = 0
        for row in vocabulary.types:
            incoming = (row.inverse, row.symmetric, row.category, row.canonical)
            current = existing_types.get(row.type)
            if current is None:
                shadowed = existing_synonyms.get(row.type)
                if shadowed is not None and shadowed != row.type:
                    raise InvalidBundleError(
                        [f"bundled relationship type would shadow a destination synonym: {row.type}"]
                    )
                self._conn.execute(
                    """INSERT INTO relationship_types (type, inverse, symmetric, category, canonical)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row.type, row.inverse, int(row.symmetric), row.category, int(row.canonical)),
                )
                existing_types[row.type] = incoming
                inserted_types += 1
            elif current != incoming:
                raise InvalidBundleError(
                    [f"bundled relationship type conflicts with the destination vocabulary: {row.type}"]
                )

        inserted_synonyms = 0
        for synonym_row in vocabulary.synonyms:
            current_type = existing_synonyms.get(synonym_row.synonym)
            if current_type is None:
                if synonym_row.synonym != synonym_row.type and synonym_row.synonym in existing_types:
                    raise InvalidBundleError(
                        [f"bundled relationship synonym is already a known type: {synonym_row.synonym}"]
                    )
                self._conn.execute(
                    "INSERT INTO relationship_type_synonyms (synonym, type) VALUES (?, ?)",
                    (synonym_row.synonym, synonym_row.type),
                )
                existing_synonyms[synonym_row.synonym] = synonym_row.type
                inserted_synonyms += 1
            elif current_type != synonym_row.type:
                raise InvalidBundleError(
                    [f"bundled relationship synonym conflicts with the destination vocabulary: {synonym_row.synonym}"]
                )
        return inserted_types, inserted_synonyms

    def _insert_devices(self, document: SyncBundleDocument) -> None:
        """Write every bundled device as history; the destination stays the only active one."""
        self._conn.executemany(
            """INSERT INTO devices
               (id, display_name, public_key, created_at, retired_at, hlc_physical_ms, hlc_logical)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [_device_row(device, document) for device in document.devices],
        )

    def _insert_domain(self, document: SyncBundleDocument) -> None:
        snapshot = document.snapshot
        # `name_normalized` is the only column organization lookup searches. Leaving it NULL
        # would restore rows that `SetAffiliation` cannot find by name, silently duplicating
        # every restored organization on the next affiliation write.
        self._insert_many(
            "organizations",
            ("id", "name", "name_normalized", "kind"),
            ((row.id, row.name, normalize_name(row.name), row.kind) for row in snapshot.organizations),
        )
        self._insert_many(
            "persons",
            ("id", "canonical_name", "canonical_name_normalized", "is_self", "summary",
             "created_at", "updated_at", "deleted_at"),
            (
                (
                    row.id,
                    row.canonical_name,
                    normalize_name(row.canonical_name),
                    int(row.is_self),
                    row.summary,
                    row.created_at.isoformat(),
                    row.updated_at.isoformat(),
                    row.deleted_at.isoformat() if row.deleted_at else None,
                )
                for row in snapshot.people
            ),
        )
        self._insert_many(
            "aliases",
            ("id", "person_id", "value", "value_normalized", "kind", "lang", "script"),
            (
                (alias.id, person.id, alias.value, normalize_name(alias.value), alias.kind.value, alias.lang,
                 alias.script)
                for person in snapshot.people
                for alias in person.aliases
            ),
        )
        self._insert_many(
            "affiliations",
            ("id", "person_id", "org_id", "role", "valid_from", "valid_to", "confidence",
             "provenance_source", "provenance_session", "provenance_stated_by", "created_at"),
            (
                (row.id, row.person_id, row.org_id, row.role, *_period(row.period), row.confidence,
                 *_provenance(row.provenance), row.created_at.isoformat())
                for row in snapshot.affiliations
            ),
        )
        self._insert_many(
            "relationships",
            ("id", "subject_id", "object_id", "type", "label", "valid_from", "valid_to", "confidence",
             "provenance_source", "provenance_session", "provenance_stated_by", "created_at"),
            (
                (row.id, row.subject_id, row.object_id, row.type, row.label, *_period(row.period), row.confidence,
                 *_provenance(row.provenance), row.created_at.isoformat())
                for row in snapshot.relationships
            ),
        )
        self._insert_many(
            "facts",
            ("id", "person_id", "predicate", "value", "valid_from", "valid_to", "recorded_at", "confidence",
             "sensitivity", "provenance_source", "provenance_session", "provenance_stated_by"),
            (
                (row.id, row.person_id, row.predicate, row.value, *_period(row.period),
                 row.recorded_at.isoformat(), row.confidence, row.sensitivity.value, *_provenance(row.provenance))
                for row in snapshot.facts
            ),
        )
        self._insert_many(
            "observations",
            ("id", "person_id", "text", "observed_at", "sensitivity",
             "provenance_source", "provenance_session", "provenance_stated_by"),
            (
                (row.id, row.person_id, row.text, row.observed_at.isoformat(), row.sensitivity.value,
                 *_provenance(row.provenance))
                for row in snapshot.observations
            ),
        )
        self._insert_many(
            "traits",
            ("id", "person_id", "category", "value", "evidence_note", "confidence", "sensitivity",
             "provenance_source", "provenance_session", "provenance_stated_by", "updated_at"),
            (
                (row.id, row.person_id, row.category.value, row.value, row.evidence_note, row.confidence,
                 row.sensitivity.value, *_provenance(row.provenance), row.updated_at.isoformat())
                for row in snapshot.traits
            ),
        )
        self._insert_many(
            "interactions",
            ("id", "summary", "occurred_at", "channel", "sensitivity",
             "provenance_source", "provenance_session", "provenance_stated_by"),
            (
                (row.id, row.summary, row.occurred_at.isoformat(), row.channel, row.sensitivity.value,
                 *_provenance(row.provenance))
                for row in snapshot.interactions
            ),
        )
        self._insert_many(
            "interaction_participants",
            ("interaction_id", "person_id"),
            (
                (row.id, person_id)
                for row in snapshot.interactions
                for person_id in row.participant_ids
            ),
        )
        self._insert_many(
            "reminders",
            ("id", "person_id", "text", "kind", "due_at", "recurrence", "status", "created_at"),
            (
                (row.id, row.person_id, row.text, row.kind.value,
                 row.due_at.isoformat() if row.due_at else None, row.recurrence, row.status.value,
                 row.created_at.isoformat())
                for row in snapshot.reminders
            ),
        )
        self._insert_many(
            "user_preferences",
            ("key", "value_json", "updated_at"),
            (
                (row.key, json.dumps(row.value, ensure_ascii=False, sort_keys=True), row.updated_at.isoformat())
                for row in snapshot.user_preferences
            ),
        )
        self._insert_many(
            "audit_log",
            ("id", "ts", "op", "entity_type", "entity_id", "payload_json", "source"),
            (
                (row.id, row.ts.isoformat(), row.op, row.entity_type, row.entity_id,
                 json.dumps(row.payload, ensure_ascii=False, sort_keys=True), row.source)
                for row in snapshot.audit_log
            ),
        )

    def _insert_imports(self, document: SyncBundleDocument) -> None:
        """Restore source receipts, commit mappings, and incomplete staging verbatim.

        A version-1 bundle carries none of this and simply writes nothing here.
        """
        imports = document.imports
        self._insert_many(
            "import_source_sessions",
            ("id", "source_kind", "label", "external_source_id", "content_digest", "extraction_fingerprint",
             "extraction_contract_revision", "claim_key", "batch_id", "status", "created_at"),
            (
                (row.id, row.source_kind, row.label, row.external_source_id, row.content_digest,
                 row.extraction_fingerprint, row.extraction_contract_revision, row.claim_key, row.batch_id,
                 row.status, row.created_at.isoformat())
                for row in imports.source_sessions
            ),
        )
        self._insert_many(
            "import_candidate_mappings",
            ("candidate_id", "batch_id", "source_session_id", "disposition", "entity_type", "entity_id",
             "created_at"),
            (
                (row.candidate_id, row.batch_id, row.source_session_id, row.disposition, row.entity_type,
                 row.entity_id, row.created_at.isoformat())
                for row in imports.candidate_mappings
            ),
        )
        self._insert_many(
            "import_staging",
            ("id", "batch_id", "source", "candidate_json", "status", "created_at"),
            (
                (row.id, row.batch_id, row.source, json.dumps(row.candidate, ensure_ascii=False, sort_keys=True),
                 row.status, row.created_at.isoformat())
                for row in imports.staging
            ),
        )

    def _insert_changelog(self, document: SyncBundleDocument) -> None:
        self._insert_many(
            "changelog",
            ("op_id", "device_id", "hlc_physical_ms", "hlc_logical", "transaction_id", "entity_type",
             "entity_id", "op_kind", "payload_json", "changed_fields_json", "actor_json", "schema_version",
             "inserted_at"),
            (
                (row.op_id, row.device_id, row.hlc_physical_ms, row.hlc_logical, row.transaction_id,
                 row.entity_type, row.entity_id, row.op_kind,
                 json.dumps(row.payload, ensure_ascii=False, sort_keys=True),
                 json.dumps(row.changed_fields, ensure_ascii=False),
                 json.dumps(row.actor, ensure_ascii=False, sort_keys=True),
                 row.schema_version, row.inserted_at.isoformat())
                for row in document.changelog
            ),
        )

    def _insert_many(self, table: str, columns: tuple[str, ...], rows: Iterable[tuple[Any, ...]]) -> None:
        placeholders = ", ".join("?" for _ in columns)
        self._conn.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608 - fixed constants
            list(rows),
        )

def _difference_count(actual: dict[str, Any], expected: dict[str, Any]) -> int:
    return len({key for key in actual.keys() | expected.keys() if actual.get(key) != expected.get(key)})


def _device_row(device: BundleDevice, document: SyncBundleDocument) -> tuple[Any, ...]:
    # The origin device is active on its own machine but is only history here, so an absent
    # retirement instant is stamped with the bundle's own deterministic creation time.
    retired_at = device.retired_at or document.created_at
    return (
        device.id,
        device.display_name,
        device.public_key,
        device.created_at.isoformat(),
        retired_at.isoformat(),
        device.hlc_physical_ms,
        device.hlc_logical,
    )


def _period(period: BundleValidityPeriod) -> tuple[str | None, str | None]:
    return (
        period.valid_from.isoformat() if period.valid_from else None,
        period.valid_to.isoformat() if period.valid_to else None,
    )


def _provenance(provenance: BundleProvenance) -> tuple[str, str | None, str | None]:
    return (provenance.source, provenance.session, provenance.stated_by)


def _outcome(
    document: SyncBundleDocument,
    relationship_types: int,
    relationship_synonyms: int,
    indexed_names: int,
    watermark: HlcTimestamp,
) -> RestoreOutcome:
    snapshot = document.snapshot
    return RestoreOutcome(
        people=len(snapshot.people),
        organizations=len(snapshot.organizations),
        affiliations=len(snapshot.affiliations),
        relationships=len(snapshot.relationships),
        facts=len(snapshot.facts),
        observations=len(snapshot.observations),
        traits=len(snapshot.traits),
        interactions=len(snapshot.interactions),
        reminders=len(snapshot.reminders),
        user_preferences=len(snapshot.user_preferences),
        audit_entries=len(snapshot.audit_log),
        relationship_types=relationship_types,
        relationship_synonyms=relationship_synonyms,
        devices=len(document.devices),
        changelog_entries=len(document.changelog),
        indexed_names=indexed_names,
        local_watermark=watermark,
        source_sessions=len(document.imports.source_sessions),
        candidate_mappings=len(document.imports.candidate_mappings),
        staged_candidates=len(document.imports.staging),
    )
