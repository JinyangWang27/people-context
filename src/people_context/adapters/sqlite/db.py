"""SQLite connection setup and migration runner.

`open_db` is the plaintext default. `open_encrypted_db` is a separate, opt-in
entry point backed by SQLCipher; encryption is deliberately not a parameter of
`open_db`, so no existing caller can be flipped between the two by accident.
Both return an opaque DB-API connection that the rest of the adapter layer uses
identically.
"""

from __future__ import annotations

import importlib
import os
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from people_context.adapters.filesystem.private_file import PRIVATE_FILE_MODE
from people_context.domain.shared import new_id, normalize_name

_MIGRATIONS_PACKAGE = "people_context.adapters.sqlite.migrations"
_LEADING_NUMBER = re.compile(r"^(\d+)")

#: Import name provided by the `encrypted` extra's binding.
SQLCIPHER_MODULE = "sqlcipher3.dbapi2"

#: Platforms the `encrypted` extra installs a wheel for. `sqlcipher3-binary`
#: publishes manylinux x86_64 wheels only and no source distribution, so the
#: extra is marked for Linux x86_64 and documented as limited to it rather than
#: claimed everywhere. PEP 508 has no libc marker, so musl-based Linux is not
#: covered by those wheels either. Any other platform can supply a compatible
#: `sqlcipher3` build itself.
ENCRYPTED_EXTRA_PLATFORMS = "glibc-based Linux x86_64"


class EncryptedDatabaseError(RuntimeError):
    """Raised when an encrypted database cannot be opened.

    The message is deliberately generic: it never contains key material and
    never echoes decrypted page contents.
    """


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite database and run pending migrations.

    Accepts ":memory:" as well as filesystem paths. Parent directories are
    created for real paths. Sets Row factory and foreign-key / WAL pragmas.
    """
    target, is_memory = _resolve_target(path)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    _configure_and_migrate(conn, is_memory=is_memory)
    return conn


def open_encrypted_db(path: str | Path, key: str) -> sqlite3.Connection:
    """Open a SQLCipher-encrypted database and run pending migrations.

    Keys the connection before any schema metadata is read, then applies exactly
    the same Row factory, foreign-key, WAL, busy-timeout, and migration setup as
    :func:`open_db`, so a new and an existing encrypted database both reach the
    canonical migration path. The returned connection is DB-API compatible but
    is not a `sqlite3.Connection`; callers must treat it as opaque.

    Raises `EncryptedDatabaseError` for an unusable key, a missing binding, or a
    database that cannot be decrypted. Never falls back to plaintext.
    """
    if not key.strip():
        raise EncryptedDatabaseError("Refusing to open an encrypted database with an empty key.")

    dbapi = _load_sqlcipher()
    target, is_memory = _resolve_target(path)
    conn = dbapi.connect(target)
    try:
        # SQLCipher requires the key before the header is read; nothing above
        # this line may touch schema metadata.
        conn.execute(f"PRAGMA key = {_quote_key(key)}")
        # Cheapest read that forces header decryption, so a wrong key fails here
        # rather than part-way through a migration.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception:
        conn.close()
        # `from None` on purpose: the underlying driver error is not chained, so
        # no decrypted page detail or key material reaches a traceback or log.
        raise EncryptedDatabaseError(
            "Cannot open the encrypted database: it is not a SQLCipher database or the key is wrong."
        ) from None
    conn.row_factory = dbapi.Row
    try:
        _configure_and_migrate(conn, is_memory=is_memory)
    except Exception:
        conn.close()
        raise
    return conn


def _load_sqlcipher() -> Any:
    """Import the optional SQLCipher binding or explain how to install it."""
    try:
        return importlib.import_module(SQLCIPHER_MODULE)
    except ImportError as exc:
        raise EncryptedDatabaseError(
            "Encrypted mode requires the optional SQLCipher binding. "
            "Install it with `pip install 'people-context[encrypted]'` "
            f"(prebuilt wheels are published for {ENCRYPTED_EXTRA_PLATFORMS}; "
            "other platforms need a locally built `sqlcipher3`)."
        ) from exc


def _quote_key(key: str) -> str:
    """Return `key` as a SQL string literal for the `PRAGMA key` statement.

    `PRAGMA` does not accept bound parameters, so the passphrase is escaped as a
    single-quoted literal. This is the only place key material is interpolated.
    """
    escaped = key.replace("'", "''")
    return f"'{escaped}'"


def _resolve_target(path: str | Path) -> tuple[str, bool]:
    """Return the connect target and whether it is the in-memory database."""
    if str(path) == ":memory:":
        return ":memory:", True
    db_path = Path(path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _precreate_owner_private_db(db_path)
    return str(db_path), False


def _precreate_owner_private_db(db_path: Path) -> None:
    """Create a missing database file with owner-only permissions.

    Left to itself SQLite creates a missing database with `0o666 & ~umask`, which
    under the common `022` umask publishes the single most sensitive file this
    project owns to every other local account. Creating the empty file first with
    `O_CREAT | O_EXCL` and mode `0o600` means SQLite opens a file that already
    exists and leaves its mode alone; a zero-length file is a valid empty
    database, and the `-wal` and `-shm` files SQLite derives from it inherit the
    same permissions.

    An existing database keeps whatever mode it already has. Silently tightening
    a file the operator placed themselves would break a deliberate arrangement,
    so widening protection for existing stores stays an explicit `chmod`.
    """
    try:
        handle = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, PRIVATE_FILE_MODE)
    except FileExistsError:
        return
    os.close(handle)


def _configure_and_migrate(conn: sqlite3.Connection, *, is_memory: bool) -> None:
    """Apply the shared pragmas and bring the schema up to date."""
    if not is_memory:
        # WAL is a persistent-file feature; skip (harmless) failures on :memory:.
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Wait for concurrent writers (e.g. CLI beside a running server) instead of
    # failing immediately with "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    # Domain name normalization exposed to migration SQL for backfilling
    # normalized columns (e.g. migration 004).
    conn.create_function("people_normalize", 1, normalize_name, deterministic=True)
    _run_migrations(conn)
    _ensure_local_device(conn)


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


def latest_schema_version() -> int:
    """Return the highest migration version this release ships."""
    migrations = _discover_migrations()
    return migrations[-1][0] if migrations else 0


#: Tables that identify a database as this project's rather than some other application's.
#: `persons` has existed since the first migration and `devices` since the second, so any store
#: at the current schema has both.
_IDENTIFYING_TABLES = ("persons", "devices")

_IDENTITY_SQL = (
    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)"
)


@dataclass(frozen=True)
class StoredSchema:
    """What a read-only look at an existing database says about opening it.

    `version` is the stored `user_version`; a value below `latest_schema_version()` means
    opening would migrate. `is_people_context` says whether the file is this project's store at
    all — an unrelated SQLite database can carry any `user_version`, including one that looks
    current, and opening it would rewrite its journal mode before failing on a table it never
    had.
    """

    version: int
    is_people_context: bool


def inspect_schema(path: str | Path, key: str | None = None) -> StoredSchema | None:
    """Describe an existing database without creating, migrating, or otherwise touching it.

    Both openers bring a database up to date as a side effect of opening it, which is right for
    every command that goes on to use the store. A caller that must know whether opening would
    *change* something has no way to ask them, so this asks SQLite directly over a read-only
    connection: no file is created, no migration runs, no journal mode is switched, and no
    device row is registered.

    Returns `None` when there is no readable database to answer for — an absent or unreadable
    file, something that is not a SQLite database, or an encrypted one the key does not open.
    Those are reported the same way by design: the caller learns only that it cannot proceed,
    which is all it needs, and no driver error text reaches a message.
    """
    if str(path) == ":memory:":
        return None
    connect = sqlite3.connect
    if key is not None:
        connect = _load_sqlcipher().connect
    try:
        conn = connect(_read_only_uri(path), uri=True)
    except Exception:  # noqa: BLE001 - absence and permission both mean "cannot answer"
        return None
    try:
        if key is not None:
            conn.execute(f"PRAGMA key = {_quote_key(key)}")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        found = conn.execute(_IDENTITY_SQL, _IDENTIFYING_TABLES).fetchone()[0]
    except Exception:  # noqa: BLE001 - not a database, or the key does not open it
        return None
    else:
        return StoredSchema(version=version, is_people_context=found == len(_IDENTIFYING_TABLES))
    finally:
        conn.close()


def _read_only_uri(path: str | Path) -> str:
    """Return a read-only `file:` URI naming exactly `path`.

    The filename is percent-encoded rather than interpolated. `?` and `#` are ordinary
    characters in a POSIX filename and URI syntax in a SQLite URI, so interpolating a path
    containing one silently renames the target and drops `mode=ro` into the query string it
    started — which would open a different file, read-write, and create it. Encoding first is
    what keeps "creates nothing" true for every path a caller may pass.

    `absolute()` rather than `resolve()`: `as_uri()` only requires an absolute path, and
    resolving would follow symlinks and change which file is being described.
    """
    # Deliberately not `_resolve_target`: that creates parent directories, which is exactly the
    # kind of side effect a caller reaches for this function to avoid.
    return f"{Path(path).expanduser().absolute().as_uri()}?mode=ro"


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
