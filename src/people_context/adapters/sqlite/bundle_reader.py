"""SQLite single-snapshot reader for bootstrap sync bundles."""

from __future__ import annotations

import sqlite3
from typing import Any

from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
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
        return BundleSource(
            origin_device_id=origin["id"],
            watermark=HlcTimestamp(origin["hlc_physical_ms"], origin["hlc_logical"]),
            devices=devices,
            snapshot=snapshot,
            relationship_types=relationship_types,
            relationship_synonyms=relationship_synonyms,
            changelog=changelog,
        )

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
