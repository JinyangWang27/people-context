"""SQLite aggregate queries behind the local inventory report.

Every statement here is a `COUNT(*)`, and every projected column is a grouping key rather than
record content — an alias kind, a sensitivity level, a relationship category, an audit
operation, or an opaque device id. No statement selects a name, a value, a summary, a device
display name, or a path, so the adapter cannot return record content even by accident.

Two of those keys are not vocabulary this project controls: a relationship category is typed by
the operator, and a restored audit operation comes from whichever installation wrote the
bundle. Both are folded into sentinels here, at the last point before they would cross the
port, so an authored string is counted but never named.

The database path is used only to measure files. It is never returned; whether the operator
sees it is a decision the application makes from the path the process resolved for itself.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.ports.audit_log import KNOWN_AUDIT_OPERATIONS
from people_context.ports.stats import (
    CUSTOM_RELATIONSHIP_CATEGORY,
    DOCUMENTED_TABLES,
    IMPORTED_DEVICE_PREFIX,
    OTHER_AUDIT_OPERATION,
    SEEDED_RELATIONSHIP_CATEGORIES,
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

# A stored type with no vocabulary row has no category at all, which is exactly the drift
# `pctx normalize-relationships` exists to resolve; the NULL is bucketed rather than silently
# dropped from the distribution. The raw category is projected and bucketed in Python so the
# two sentinels stay distinguishable: a type outside the vocabulary is a different situation
# from a category the operator invented, and collapsing both in SQL would conflate them.
_RELATIONSHIP_CATEGORIES_SQL = """
SELECT rt.category AS bucket, COUNT(*) AS total
FROM relationships r
LEFT JOIN relationship_types rt ON rt.type = r.type
GROUP BY rt.category
"""

_AUDIT_OPERATIONS_SQL = "SELECT op AS bucket, COUNT(*) AS total FROM audit_log GROUP BY op"

# Grouped by the device's id. The `devices` table is deliberately not joined for counting: its
# `display_name` is a hostname, which is the one piece of identifying text in the sync tables.
_CHANGELOG_DEVICES_SQL = "SELECT device_id AS bucket, COUNT(*) AS total FROM changelog GROUP BY device_id"

# This installation's own identity. Restore writes every imported device retired and never
# retires or overwrites the destination's own row, so a non-retired device is one minted here
# by `_ensure_local_device` — the only provenance signal the schema actually offers.
_LOCAL_DEVICES_SQL = "SELECT id FROM devices WHERE retired_at IS NULL"


class SqliteStatsReader:
    """Read aggregate counts and storage bytes from the local SQLite store."""

    def __init__(self, conn: sqlite3.Connection, path: str | Path) -> None:
        self._conn = conn
        self._path = path

    def read_inventory(self) -> StoreInventory:
        """Return every documented aggregate from one committed snapshot.

        The counts are read inside a single transaction. Left in autocommit they would be a
        dozen independent reads, and a writer committing between two of them — the MCP server
        running beside this CLI is a supported arrangement — would produce a report that
        contradicts itself: more `persons` rows than people, or a distribution whose buckets
        do not add up to their own table. The unit of work joins an outer transaction if the
        caller already opened one, so nesting stays safe.
        """
        with SqliteUnitOfWork(self._conn):
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
                relationship_categories=self._buckets(
                    _RELATIONSHIP_CATEGORIES_SQL, bucket=_relationship_category
                ),
                audit_operations=self._buckets(_AUDIT_OPERATIONS_SQL, bucket=_audit_operation),
                changelog_devices=self._devices(),
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

    def _buckets(
        self,
        sql: str,
        *,
        bucket: Callable[[str | None], str] | None = None,
    ) -> dict[str, int]:
        """Return one grouped distribution, optionally folding open keys into sentinels.

        Counts are summed rather than assigned, because folding is many-to-one: every custom
        category collapses into a single bucket, and the total has to survive that.
        """
        rows: Iterable[tuple[str | None, int]] = (
            (row["bucket"], row["total"]) for row in self._conn.execute(sql).fetchall()
        )
        if bucket is None:
            return {key: total for key, total in rows if key is not None}
        folded: Counter[str] = Counter()
        for key, total in rows:
            folded[bucket(key)] += total
        return dict(folded)

    def _devices(self) -> dict[str, int]:
        """Count changelog entries per device, naming only this installation's own id.

        Unlike the other folds this one is one-to-one: collapsing imported devices into a
        single bucket would answer "how many entries came from elsewhere" when the
        distribution exists to answer "how many devices, and how much from each". Only the
        local id is known to be opaque, because this installation minted it; an imported id is
        whatever its origin wrote, and being a well-formed identifier does not make it any
        less something someone chose. Pseudonyms are numbered in sorted id order, so the same
        store reports the same names on every run.
        """
        counts = self._buckets(_CHANGELOG_DEVICES_SQL)
        local = {row["id"] for row in self._conn.execute(_LOCAL_DEVICES_SQL).fetchall()}
        pseudonyms = {
            device_id: f"{IMPORTED_DEVICE_PREFIX}{position}"
            for position, device_id in enumerate(sorted(key for key in counts if key not in local), start=1)
        }
        return {pseudonyms.get(device_id, device_id): total for device_id, total in counts.items()}

    def _storage(self) -> StorageFootprint:
        """Measure the main database file plus any WAL companions beside it.

        The path is resolved first. SQLite derives the `-wal` and `-shm` names from the file it
        actually opened, so when `--db` names a symlink the companions are created beside the
        *target*. Probing beside the link would find nothing and quietly report zero for both,
        which is worse than an error: `stat()` follows the link, so the main file still
        measures and the total looks entirely plausible while omitting a live WAL that can be
        larger than the database.
        """
        if str(self._path) == ":memory:":
            return StorageFootprint(STORAGE_MEMORY)
        main = Path(self._path).expanduser().resolve()
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


def _relationship_category(category: str | None) -> str:
    """Bucket one stored relationship category without naming an operator's own wording."""
    if category is None:
        return UNCATEGORIZED_RELATIONSHIP
    if category in SEEDED_RELATIONSHIP_CATEGORIES:
        return category
    return CUSTOM_RELATIONSHIP_CATEGORY


def _audit_operation(operation: str | None) -> str:
    """Bucket one stored audit operation, counting an unrecognized one without naming it."""
    if operation in KNOWN_AUDIT_OPERATIONS:
        # `operation` is a member of the known set, so it is not None.
        return str(operation)
    return OTHER_AUDIT_OPERATION


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
