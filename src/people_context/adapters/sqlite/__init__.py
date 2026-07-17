"""SQLite persistence adapter."""

from __future__ import annotations

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.import_staging import SqliteImportStagingStore
from people_context.adapters.sqlite.lifecycle import SqliteLifecycleStore
from people_context.adapters.sqlite.record_store import (
    SqliteOrganizationStore,
    SqlitePreferencesStore,
    SqliteRecordStore,
)
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.adapters.sqlite.semantic import (
    SqliteSemanticDocumentReader,
    SqliteVectorIndex,
    create_sqlite_vector_index,
    read_semantic_metadata,
)

__all__ = [
    "SqliteAuditLog",
    "SqliteContextReader",
    "SqliteExportReader",
    "SqliteImportStagingStore",
    "SqliteLifecycleStore",
    "SqliteOrganizationStore",
    "SqlitePeopleRepository",
    "SqlitePreferencesStore",
    "SqliteRecordStore",
    "SqliteSemanticDocumentReader",
    "SqliteVectorIndex",
    "create_sqlite_vector_index",
    "open_db",
    "read_semantic_metadata",
]
