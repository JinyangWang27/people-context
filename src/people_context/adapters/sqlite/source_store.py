"""SQLite persistence for import source receipts and candidate commit outcomes.

Claiming a source and publishing its batch is one write, not two. A check-then-insert would let
two processes both observe "no claim yet" and both stage the same export, so the claim is taken
under an immediate write reservation and backed by a UNIQUE index that refuses the loser even if
the reservation is somehow bypassed. The loser writes nothing at all and reports the winner's
session, which is what makes re-running an import safe rather than duplicative.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.import_staging import SqliteImportStagingStore
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.imports import StagedImportRow
from people_context.ports.sources import (
    STATUS_STAGED,
    CandidateMappingRow,
    SourceClaimOutcome,
    SourceSessionClaim,
    SourceSessionRow,
)

_SESSION_COLUMNS = (
    "id, source_kind, label, external_source_id, content_digest, extraction_fingerprint, "
    "extraction_contract_revision, claim_key, batch_id, status, created_at"
)

_MAPPING_COLUMNS = (
    "candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at"
)


class SqliteImportSourceStore:
    """Persist source sessions and candidate mappings on one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._staging = SqliteImportStagingStore(conn)

    @property
    def unit_of_work(self) -> SqliteUnitOfWork:
        """Return a write-reserving boundary, because every claim reads before it writes."""
        return SqliteUnitOfWork(self._conn, immediate=True)

    @property
    def audit_log(self) -> SqliteAuditLog:
        """Expose the paired mutation journal for app construction."""
        return SqliteAuditLog(self._conn)

    def claim_and_stage(
        self,
        claim: SourceSessionClaim,
        rows: list[StagedImportRow],
        *,
        session_id: str,
        batch_id: str,
        created_at: datetime,
    ) -> SourceClaimOutcome:
        """Take the canonical claim and publish the batch, or report the claim's existing owner."""
        with SqliteUnitOfWork(self._conn, immediate=True):
            claim_key = claim.claim_key
            if claim_key is not None:
                owner = self._session_by_claim(claim_key)
                if owner is not None:
                    return SourceClaimOutcome(
                        session=owner,
                        created=False,
                        candidate_count=self._batch_count(owner.batch_id),
                    )
            session = SourceSessionRow(
                id=session_id,
                source_kind=claim.source_kind,
                label=claim.label,
                external_source_id=claim.external_source_id,
                content_digest=claim.content_digest,
                extraction_fingerprint=claim.extraction_fingerprint,
                extraction_contract_revision=claim.extraction_contract_revision,
                claim_key=claim_key,
                batch_id=batch_id,
                status=STATUS_STAGED,
                created_at=created_at,
            )
            self._conn.execute(
                f"INSERT INTO import_source_sessions ({_SESSION_COLUMNS}) "  # noqa: S608 - fixed constants
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    session.source_kind,
                    session.label,
                    session.external_source_id,
                    session.content_digest,
                    session.extraction_fingerprint,
                    session.extraction_contract_revision,
                    session.claim_key,
                    session.batch_id,
                    session.status,
                    session.created_at.isoformat(),
                ),
            )
            self._staging.stage_batch(rows)
        return SourceClaimOutcome(session=session, created=True, candidate_count=len(rows))

    def get_session(self, session_id: str) -> SourceSessionRow | None:
        """Return one receipt by id."""
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM import_source_sessions WHERE id = ?",  # noqa: S608
            (session_id,),
        ).fetchone()
        return _session(row) if row is not None else None

    def session_for_batch(self, batch_id: str) -> SourceSessionRow | None:
        """Return the receipt one staged batch belongs to, if it is source-tracked."""
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM import_source_sessions WHERE batch_id = ?",  # noqa: S608
            (batch_id,),
        ).fetchone()
        return _session(row) if row is not None else None

    def set_session_status(self, session_id: str, status: str) -> None:
        """Advance one receipt's reviewability status."""
        with SqliteUnitOfWork(self._conn):
            self._conn.execute(
                "UPDATE import_source_sessions SET status = ? WHERE id = ?",
                (status, session_id),
            )

    def record_mappings(self, mappings: list[CandidateMappingRow]) -> None:
        """Persist one commit's candidate outcomes.

        A retried commit never reaches an already-committed candidate, so a mapping is written
        once. The upsert is a belt-and-braces guard that keeps a re-run idempotent rather than
        failing on the primary key.
        """
        if not mappings:
            return
        with SqliteUnitOfWork(self._conn):
            self._conn.executemany(
                f"INSERT INTO import_candidate_mappings ({_MAPPING_COLUMNS}) "  # noqa: S608 - fixed constants
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(candidate_id) DO UPDATE SET "
                "disposition = excluded.disposition, entity_type = excluded.entity_type, "
                "entity_id = excluded.entity_id",
                [
                    (
                        mapping.candidate_id,
                        mapping.batch_id,
                        mapping.source_session_id,
                        mapping.disposition,
                        mapping.entity_type,
                        mapping.entity_id,
                        mapping.created_at.isoformat(),
                    )
                    for mapping in mappings
                ],
            )

    def mappings_for_batch(self, batch_id: str) -> list[CandidateMappingRow]:
        """Return every stored outcome of one batch, in deterministic candidate order."""
        rows = self._conn.execute(
            f"SELECT {_MAPPING_COLUMNS} FROM import_candidate_mappings "  # noqa: S608 - fixed constants
            "WHERE batch_id = ? ORDER BY candidate_id",
            (batch_id,),
        ).fetchall()
        return [_mapping(row) for row in rows]

    def _session_by_claim(self, claim_key: str) -> SourceSessionRow | None:
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM import_source_sessions WHERE claim_key = ?",  # noqa: S608
            (claim_key,),
        ).fetchone()
        return _session(row) if row is not None else None

    def _batch_count(self, batch_id: str | None) -> int:
        if batch_id is None:
            return 0
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM import_staging WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return int(row["total"])


def _session(row: sqlite3.Row) -> SourceSessionRow:
    return SourceSessionRow(
        id=row["id"],
        source_kind=row["source_kind"],
        label=row["label"],
        external_source_id=row["external_source_id"],
        content_digest=row["content_digest"],
        extraction_fingerprint=row["extraction_fingerprint"],
        extraction_contract_revision=row["extraction_contract_revision"],
        claim_key=row["claim_key"],
        batch_id=row["batch_id"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _mapping(row: sqlite3.Row) -> CandidateMappingRow:
    return CandidateMappingRow(
        candidate_id=row["candidate_id"],
        batch_id=row["batch_id"],
        source_session_id=row["source_session_id"],
        disposition=row["disposition"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
