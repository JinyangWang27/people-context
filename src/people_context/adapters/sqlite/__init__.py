"""SQLite persistence adapter."""

from __future__ import annotations

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.forget_store import SqliteForgetStore
from people_context.adapters.sqlite.graph_reader import SqliteGraphReader
from people_context.adapters.sqlite.hlc import SqliteHybridLogicalClock
from people_context.adapters.sqlite.import_staging import SqliteImportStagingStore
from people_context.adapters.sqlite.merge_store import SqliteMergeStore
from people_context.adapters.sqlite.organization_store import SqliteOrganizationStore
from people_context.adapters.sqlite.preferences_store import SqlitePreferencesStore
from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.relationship_store import SqliteRelationshipStore
from people_context.adapters.sqlite.relationship_vocabulary import SqliteRelationshipVocabularyStore
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.adapters.sqlite.semantic import (
    SqliteSemanticDocumentReader,
    SqliteSemanticEntityReader,
    SqliteSemanticMetadataReader,
    SqliteVectorIndex,
    create_sqlite_vector_index,
    open_sqlite_vector_index,
    read_semantic_metadata,
)
from people_context.adapters.sqlite.unit_of_work import SqliteUnitOfWork
from people_context.adapters.sqlite.vault_reader import SqliteVaultReader

__all__ = [
    "SqliteAuditLog",
    "SqliteChangelog",
    "SqliteContextReader",
    "SqliteExportReader",
    "SqliteGraphReader",
    "SqliteHybridLogicalClock",
    "SqliteImportStagingStore",
    "SqliteForgetStore",
    "SqliteMergeStore",
    "SqliteOrganizationStore",
    "SqlitePeopleRepository",
    "SqlitePreferencesStore",
    "SqliteRecordStore",
    "SqliteRelationshipStore",
    "SqliteRelationshipVocabularyStore",
    "SqliteSemanticDocumentReader",
    "SqliteSemanticEntityReader",
    "SqliteSemanticMetadataReader",
    "SqliteUnitOfWork",
    "SqliteVaultReader",
    "SqliteVectorIndex",
    "create_sqlite_vector_index",
    "open_db",
    "open_sqlite_vector_index",
    "read_semantic_metadata",
]
