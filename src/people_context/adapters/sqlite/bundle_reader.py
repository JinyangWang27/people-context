"""SQLite single-snapshot reader for bootstrap sync bundles."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.domain.import_provenance import REVIEWABLE_SESSION_STATUSES
from people_context.ports.changelog import ChangelogEntry
from people_context.ports.hlc import HlcTimestamp
from people_context.ports.sync_bundle import BundleSource


class SqliteBundleReader:
    """Read every bundle collection from one SQLite transaction.

    A bundle must describe a single point in time, so this reader owns the
    transaction and reuses the same connection for the domain snapshot, the
    relationship vocabulary, device rows, and the complete changelog. Composing
    independently opened readers could observe different WAL snapshots.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._export = SqliteExportReader(conn)
        self._changelog = SqliteChangelog(conn)

    def read_bundle(self) -> BundleSource:
        """Return the origin identity, watermark, portable rows, vocabulary, and changelog."""
        with SqliteUnitOfWork(self._conn):
            origin = self._origin_device_row()
            snapshot = self._export.read_export()
            changelog = sorted(self._changelog.list_entries(limit=None), key=ChangelogEntry.comparison_key)
            devices = self._device_rows(origin["id"])
            relationship_types = self._relationship_types()
            relationship_synonyms = self._relationship_synonyms()
            source_sessions = self._source_sessions()
            candidate_mappings = self._candidate_mappings()
            staging = self._incomplete_staging()
        return BundleSource(
            origin_device_id=origin["id"],
            watermark=HlcTimestamp(origin["hlc_physical_ms"], origin["hlc_logical"]),
            devices=devices,
            snapshot=snapshot,
            relationship_types=relationship_types,
            relationship_synonyms=relationship_synonyms,
            changelog=changelog,
            source_sessions=source_sessions,
            candidate_mappings=candidate_mappings,
            staging=staging,
        )

    def _source_sessions(self) -> list[dict[str, Any]]:
        """Return every surviving receipt, including minimal terminal redacted ones.

        A fully forgotten digestless receipt was deleted rather than reduced, so there is nothing
        here to exclude: what the table still holds is exactly what should travel.
        """
        rows = self._conn.execute(
            """SELECT id, source_kind, label, external_source_id, content_digest, extraction_fingerprint,
                      extraction_contract_revision, claim_key, batch_id, status, created_at
               FROM import_source_sessions ORDER BY created_at, id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def _candidate_mappings(self) -> list[dict[str, Any]]:
        """Return every durable commit outcome, completed sources included.

        Mappings are primary provenance rather than operational state, so restricting them to
        incomplete batches would silently drop the record associations of every source that was
        fully committed and then cleaned up.
        """
        rows = self._conn.execute(
            """SELECT candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at
               FROM import_candidate_mappings ORDER BY source_session_id, candidate_id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def _incomplete_staging(self) -> list[dict[str, Any]]:
        """Return staging rows only for batches that still have something to review or commit.

        The statuses come from the same declaration restore validation reads, so a receipt whose
        rows this query skips cannot be one restore would accept as still holding them.
        """
        placeholders = ", ".join("?" * len(REVIEWABLE_SESSION_STATUSES))
        rows = self._conn.execute(
            f"""SELECT s.id, s.batch_id, s.source, s.candidate_json, s.status, s.created_at
               FROM import_staging s
               JOIN import_source_sessions ss ON ss.batch_id = s.batch_id
               WHERE ss.status IN ({placeholders})
               ORDER BY s.batch_id, s.created_at, s.id""",  # noqa: S608 - placeholders are counted, not interpolated
            REVIEWABLE_SESSION_STATUSES,
        ).fetchall()
        return [
            {
                "id": row["id"],
                "batch_id": row["batch_id"],
                "source": row["source"],
                "candidate": json.loads(row["candidate_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _origin_device_row(self) -> sqlite3.Row:
        row = self._conn.execute(
            """SELECT id, hlc_physical_ms, hlc_logical
               FROM devices WHERE retired_at IS NULL ORDER BY created_at, id LIMIT 1"""
        ).fetchone()
        if row is None:
            raise RuntimeError("no active local device is registered")
        return row

    def _device_rows(self, origin_device_id: str) -> list[dict[str, Any]]:
        """Return the origin device plus every device referenced by the changelog."""
        rows = self._conn.execute(
            """SELECT id, display_name, public_key, created_at, retired_at, hlc_physical_ms, hlc_logical
               FROM devices
               WHERE id = ? OR id IN (SELECT DISTINCT device_id FROM changelog)
               ORDER BY id""",
            (origin_device_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "public_key": row["public_key"],
                "created_at": row["created_at"],
                "retired_at": row["retired_at"],
                "hlc_physical_ms": row["hlc_physical_ms"],
                "hlc_logical": row["hlc_logical"],
            }
            for row in rows
        ]

    def _relationship_types(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT type, inverse, symmetric, category, canonical FROM relationship_types ORDER BY type"
        ).fetchall()
        return [
            {
                "type": row["type"],
                "inverse": row["inverse"],
                "symmetric": bool(row["symmetric"]),
                "category": row["category"],
                "canonical": bool(row["canonical"]),
            }
            for row in rows
        ]

    def _relationship_synonyms(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT synonym, type FROM relationship_type_synonyms ORDER BY synonym"
        ).fetchall()
        return [{"synonym": row["synonym"], "type": row["type"]} for row in rows]
