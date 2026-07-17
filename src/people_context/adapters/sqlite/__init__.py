"""SQLite persistence adapter."""

from __future__ import annotations

from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.repository import SqlitePeopleRepository

__all__ = ["SqliteAuditLog", "SqlitePeopleRepository", "open_db"]
