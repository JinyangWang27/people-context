"""SQLite string preference persistence."""

from __future__ import annotations

import json
import sqlite3

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.clock import Clock, SystemClock


class SqlitePreferencesStore:
    """SQLite string preference store using JSON values."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None) -> None:
        self._conn = conn
        self._clock = clock or SystemClock()

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value_json FROM user_preferences WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        value = json.loads(row["value_json"])
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        with SqliteUnitOfWork(self._conn):
            self._conn.execute(
                """INSERT INTO user_preferences (key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), self._clock.now().isoformat()),
            )
