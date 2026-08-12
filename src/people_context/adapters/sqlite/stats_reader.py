"""SQLite aggregate queries behind the local inventory report.

Every statement here is a `COUNT(*)`, and every projected column is a grouping key that is
already a closed vocabulary — an alias kind, a sensitivity level, a relationship category, an
audit operation, or an opaque device id. No statement selects a name, a value, a summary, a
device display name, or a path, so the adapter cannot return record content even by accident.

The database path is used only to measure files. It is never returned; whether the operator
sees it is a decision the application makes from the path the process resolved for itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from people_context.ports.stats import (
    DOCUMENTED_TABLES,
    STORAGE_FILE,
    STORAGE_MEMORY,
    STORAGE_UNAVAILABLE,
    UNCATEGORIZED_RELATIONSHIP,
    StorageFootprint,
    StoreInventory,
)

#: WAL companions SQLite maintains beside the main database file.
_COMPANION_SUFFIXES = ("-wal", "-shm")

_PEOPLE_SQL = """
SELECT SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS soft_deleted,
       SUM(CASE WHEN is_self = 1 THEN 1 ELSE 0 END) AS self_people
FROM persons
"""

_ALIAS_KINDS_SQL = "SELECT kind AS bucket, COUNT(*) AS total FROM aliases GROUP BY kind"

_FACT_SENSITIVITY_SQL = "SELECT sensitivity AS bucket, COUNT(*) AS total FROM facts GROUP BY sensitivity"

_OBSERVATION_SENSITIVITY_SQL = (
    "SELECT sensitivity AS bucket, COUNT(*) AS total FROM observations GROUP BY sensitivity"
)

# A stored type with no vocabulary row has no category, which is exactly the drift
# `pctx normalize-relationships` exists to resolve; it is grouped under the same sentinel the
# recency reader uses rather than silently dropped from the distribution.
_RELATIONSHIP_CATEGORIES_SQL = f"""
SELECT COALESCE(rt.category, '{UNCATEGORIZED_RELATIONSHIP}') AS bucket, COUNT(*) AS total
FROM relationships r
LEFT JOIN relationship_types rt ON rt.type = r.type
GROUP BY bucket
"""

_AUDIT_OPERATIONS_SQL = "SELECT op AS bucket, COUNT(*) AS total FROM audit_log GROUP BY op"

# Grouped by the device's opaque id. The `devices` table is deliberately not joined: its
# `display_name` is a hostname, which is the one piece of identifying text in the sync
# tables.
_CHANGELOG_DEVICES_SQL = "SELECT device_id AS bucket, COUNT(*) AS total FROM changelog GROUP BY device_id"


class SqliteStatsReader:
    """Read aggregate counts and storage bytes from the local SQLite store."""

    def __init__(self, conn: sqlite3.Connection, path: str | Path) -> None:
        self._conn = conn
        self._path = path

    def read_inventory(self) -> StoreInventory:
        """Return every documented aggregate in one snapshot."""
        people = self._conn.execute(_PEOPLE_SQL).fetchone()
        return StoreInventory(
            # SUM over an empty table yields NULL rather than 0.
            active_people=people["active"] or 0,
            soft_deleted_people=people["soft_deleted"] or 0,
            self_people=people["self_people"] or 0,
            table_rows=self._table_rows(),
            alias_kinds=self._buckets(_ALIAS_KINDS_SQL),
            fact_sensitivity=self._buckets(_FACT_SENSITIVITY_SQL),
            observation_sensitivity=self._buckets(_OBSERVATION_SENSITIVITY_SQL),
            relationship_categories=self._buckets(_RELATIONSHIP_CATEGORIES_SQL),
            audit_operations=self._buckets(_AUDIT_OPERATIONS_SQL),
            changelog_devices=self._buckets(_CHANGELOG_DEVICES_SQL),
            storage=self._storage(),
        )

    def _table_rows(self) -> dict[str, int]:
        """Count rows in each documented table.

        The table name is interpolated because SQLite cannot bind an identifier; the values
        come from the port's own literal tuple and never from a caller, an argument, or
        stored data.
        """
        return {
            table: self._conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()["total"]
            for table in DOCUMENTED_TABLES
        }

    def _buckets(self, sql: str) -> dict[str, int]:
        """Return one grouped distribution keyed by its closed-vocabulary bucket."""
        return {row["bucket"]: row["total"] for row in self._conn.execute(sql).fetchall()}

    def _storage(self) -> StorageFootprint:
        """Measure the main database file plus any WAL companions beside it."""
        if str(self._path) == ":memory:":
            return StorageFootprint(STORAGE_MEMORY)
        main = Path(self._path).expanduser()
        try:
            main_bytes = main.stat().st_size
        except OSError:
            # The file is gone, unreadable, or on an unavailable mount. Reporting zero here
            # would read as "an empty database", which is a different and wrong statement.
            return StorageFootprint(STORAGE_UNAVAILABLE)
        companions = [_optional_size(main.parent / f"{main.name}{suffix}") for suffix in _COMPANION_SUFFIXES]
        wal_bytes, shm_bytes = companions
        return StorageFootprint(
            storage_kind=STORAGE_FILE,
            database_bytes=main_bytes + wal_bytes + shm_bytes,
            main_bytes=main_bytes,
            wal_bytes=wal_bytes,
            shm_bytes=shm_bytes,
        )


def _optional_size(path: Path) -> int:
    """Return a companion file's size, treating an absent companion as zero bytes.

    A checkpointed database has no `-wal` file at all, and that genuinely contributes no
    bytes to the footprint — unlike the main file, whose absence means the measurement
    itself failed.
    """
    try:
        return path.stat().st_size
    except OSError:
        return 0
