"""Persisted SQLite hybrid logical clock."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.hlc import HlcTimestamp


class SqliteHybridLogicalClock:
    """Persist HLC state on the active installation device row."""

    def __init__(self, conn: sqlite3.Connection, wall_clock_ms: Callable[[], int] | None = None) -> None:
        self._conn = conn
        self._wall_clock_ms = wall_clock_ms or _system_time_ms

    @property
    def device_id(self) -> str:
        """Return this installation's non-retired device id."""
        return self._device_row()["id"]

    def tick(self) -> HlcTimestamp:
        """Emit a timestamp that never regresses when wall time stalls or moves backwards."""
        with SqliteUnitOfWork(self._conn):
            row = self._device_row()
            now_ms = self._wall_clock_ms()
            if now_ms > row["hlc_physical_ms"]:
                timestamp = HlcTimestamp(now_ms, 0)
            else:
                timestamp = HlcTimestamp(row["hlc_physical_ms"], row["hlc_logical"] + 1)
            self._persist(row["id"], timestamp)
        return timestamp

    def observe(self, remote: HlcTimestamp) -> HlcTimestamp:
        """Advance local state after receiving a remote timestamp."""
        with SqliteUnitOfWork(self._conn):
            row = self._device_row()
            now_ms = self._wall_clock_ms()
            local_physical = row["hlc_physical_ms"]
            local_logical = row["hlc_logical"]
            physical = max(now_ms, local_physical, remote.physical_ms)
            if physical == local_physical == remote.physical_ms:
                logical = max(local_logical, remote.logical_counter) + 1
            elif physical == local_physical:
                logical = local_logical + 1
            elif physical == remote.physical_ms:
                logical = remote.logical_counter + 1
            else:
                logical = 0
            timestamp = HlcTimestamp(physical, logical)
            self._persist(row["id"], timestamp)
        return timestamp

    def _device_row(self) -> sqlite3.Row:
        row = self._conn.execute(
            """SELECT id, hlc_physical_ms, hlc_logical
               FROM devices WHERE retired_at IS NULL ORDER BY created_at, id LIMIT 1"""
        ).fetchone()
        if row is None:
            raise RuntimeError("no active local device is registered")
        return row

    def _persist(self, device_id: str, timestamp: HlcTimestamp) -> None:
        self._conn.execute(
            "UPDATE devices SET hlc_physical_ms = ?, hlc_logical = ? WHERE id = ?",
            (timestamp.physical_ms, timestamp.logical_counter, device_id),
        )


def _system_time_ms() -> int:
    return time.time_ns() // 1_000_000
