"""SQLite connection setup and migration runner.

`open_db` is the plaintext default. `open_encrypted_db` is a separate, opt-in
entry point backed by SQLCipher; encryption is deliberately not a parameter of
`open_db`, so no existing caller can be flipped between the two by accident.
Both return an opaque DB-API connection that the rest of the adapter layer uses
identically.
"""

from __future__ import annotations

import importlib
import re
import socket
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from people_context.domain.shared import new_id, normalize_name

_MIGRATIONS_PACKAGE = "people_context.adapters.sqlite.migrations"
_LEADING_NUMBER = re.compile(r"^(\d+)")

#: Import name provided by the `encrypted` extra's binding.
SQLCIPHER_MODULE = "sqlcipher3.dbapi2"

#: Platforms the `encrypted` extra installs a wheel for. `sqlcipher3-binary`
#: publishes manylinux x86_64 wheels only and no source distribution, so the
#: extra is marked for that platform and documented as limited to it rather
#: than claimed everywhere. Other platforms can still supply a compatible
#: `sqlcipher3` build themselves.
ENCRYPTED_EXTRA_PLATFORMS = "Linux x86_64"


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
    return str(db_path), False


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
