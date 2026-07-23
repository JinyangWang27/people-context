"""Port interfaces (typing.Protocol) that adapters implement."""

from __future__ import annotations

from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.changelog import Changelog, ChangelogEntry
from people_context.ports.clock import Clock, SystemClock
from people_context.ports.context import AffiliationRecord, PersonContextReader, RelationshipRecord
from people_context.ports.export import ExportReader, ExportSnapshot
from people_context.ports.forget import ForgetPreviewStore, ForgetStore
from people_context.ports.hlc import HlcTimestamp, HybridLogicalClock
from people_context.ports.merge import MergeStore
from people_context.ports.records import OrganizationStore, PreferencesStore, Record, RecordReader, RecordWriter
from people_context.ports.repository import PersonReader, PersonSearchIndexer, PersonWriter, SearchHit
from people_context.ports.semantic import (
    EmbeddingProvider,
    SemanticDocument,
    SemanticDocumentReader,
    SemanticEntity,
    SemanticEntityReader,
    SemanticIndexMetadata,
    SemanticIndexMetadataReader,
    SemanticIndexRebuilder,
    VectorIndex,
    VectorSearchHit,
)
from people_context.ports.unit_of_work import NullUnitOfWork, UnitOfWork

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AffiliationRecord",
    "Changelog",
    "ChangelogEntry",
    "Clock",
    "ExportReader",
    "ExportSnapshot",
    "EmbeddingProvider",
    "HlcTimestamp",
    "HybridLogicalClock",
    "ForgetPreviewStore",
    "ForgetStore",
    "MergeStore",
    "NullUnitOfWork",
    "PersonReader",
    "PersonSearchIndexer",
    "PersonContextReader",
    "PersonWriter",
    "OrganizationStore",
    "PreferencesStore",
    "Record",
    "RecordReader",
    "RecordWriter",
    "SearchHit",
    "SemanticDocument",
    "SemanticDocumentReader",
    "SemanticEntity",
    "SemanticEntityReader",
    "SemanticIndexMetadata",
    "SemanticIndexMetadataReader",
    "SemanticIndexRebuilder",
    "RelationshipRecord",
    "SystemClock",
    "UnitOfWork",
    "VectorIndex",
    "VectorSearchHit",
]
