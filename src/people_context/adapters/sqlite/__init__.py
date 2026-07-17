"""SQLite persistence adapter."""

from __future__ import annotations

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.record_store import (
    SqliteOrganizationStore,
    SqlitePreferencesStore,
    SqliteRecordStore,
)
from people_context.adapters.sqlite.repository import SqlitePeopleRepository

__all__ = [
    "SqliteAuditLog",
    "SqliteContextReader",
    "SqliteOrganizationStore",
    "SqlitePeopleRepository",
    "SqlitePreferencesStore",
    "SqliteRecordStore",
    "open_db",
]
