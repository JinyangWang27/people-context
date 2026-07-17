"""Port interfaces (typing.Protocol) that adapters implement."""

from __future__ import annotations

from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.clock import Clock, SystemClock
from people_context.ports.context import AffiliationRecord, PersonContextReader, RelationshipRecord
from people_context.ports.export import ExportReader, ExportSnapshot
from people_context.ports.lifecycle import LifecycleStore
from people_context.ports.records import OrganizationStore, PreferencesStore, Record, RecordReader, RecordWriter
from people_context.ports.repository import PersonReader, PersonSearchIndexer, PersonWriter, SearchHit

__all__ = [
    "AuditEntry",
    "AuditLog",
    "AffiliationRecord",
    "Clock",
    "ExportReader",
    "ExportSnapshot",
    "LifecycleStore",
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
    "RelationshipRecord",
    "SystemClock",
]
