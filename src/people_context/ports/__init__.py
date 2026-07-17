"""Port interfaces (typing.Protocol) that adapters implement."""

from __future__ import annotations

from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.clock import Clock, SystemClock
from people_context.ports.repository import PersonReader, PersonWriter, SearchHit

__all__ = [
    "AuditEntry",
    "AuditLog",
    "Clock",
    "PersonReader",
    "PersonWriter",
    "SearchHit",
    "SystemClock",
]
