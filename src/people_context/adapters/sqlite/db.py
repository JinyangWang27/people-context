"""SQLite connection setup and migration runner."""

from __future__ import annotations

import re
import socket
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from people_context.domain.shared import new_id

_MIGRATIONS_PACKAGE = "people_context.adapters.sqlite.migrations"
_LEADING_NUMBER = re.compile(r"^(\d+)")


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite database and run pending migrations.

    Accepts ":memory:" as well as filesystem paths. Parent directories are
    created for real paths. Sets Row factory and foreign-key / WAL pragmas.
    """
    is_memory = str(path) == ":memory:"
    if not is_memory:
        db_path = Path(path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        target: str = str(db_path)
    else:
        target = ":memory:"

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    if not is_memory:
        # WAL is a persistent-file feature; skip (harmless) failures on :memory:.
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _run_migrations(conn)
    _ensure_local_device(conn)
    return conn


def _discover_migrations() -> list[tuple[int, str]]:
    """Return (version, sql) migrations sorted ascending by leading number."""
    migrations: list[tuple[int, str]] = []
    for entry in resources.files(_MIGRATIONS_PACKAGE).iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _LEADING_NUMBER.match(entry.name)
        if match is None:
            continue
        migrations.append((int(match.group(1)), entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda item: item[0])
    return migrations


def _run_migrations(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in _discover_migrations():
        if version <= current:
            continue
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise


def _ensure_local_device(conn: sqlite3.Connection) -> None:
    """Register one stable installation identity after migration 002."""
    row = conn.execute("SELECT id FROM devices WHERE retired_at IS NULL LIMIT 1").fetchone()
    if row is not None:
        return
    conn.execute(
        """INSERT INTO devices
           (id, display_name, public_key, created_at, retired_at, hlc_physical_ms, hlc_logical)
           VALUES (?, ?, NULL, ?, NULL, 0, 0)""",
        (new_id(), socket.gethostname(), datetime.now(UTC).isoformat()),
    )
    conn.commit()
