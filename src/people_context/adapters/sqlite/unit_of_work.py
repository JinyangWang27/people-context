"""Explicit SQLite unit-of-work implementation."""

from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Self


class SqliteUnitOfWork:
    """Begin, commit, or roll back a transaction while safely joining an outer one."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._owns_transaction = False

    def __enter__(self) -> Self:
        self._owns_transaction = not self._conn.in_transaction
        if self._owns_transaction:
            self._conn.execute("BEGIN")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._owns_transaction:
            return None
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return None
