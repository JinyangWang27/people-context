"""`ReviewImport` numbers rows over an explicit staging order (M29.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from people_context.app.imports import ReviewImport
from people_context.ports.imports import ImportStagingStore, StagedImportRow

_EARLY = datetime(2026, 1, 1, tzinfo=UTC)
_LATE = datetime(2026, 1, 2, tzinfo=UTC)


def _row(row_id: str, created_at: datetime, status: str = "pending") -> StagedImportRow:
    candidate: dict[str, Any] = {"type": "person", "name": row_id, "aliases": []}
    return StagedImportRow(
        id=row_id, batch_id="batch", source="notes", candidate=candidate, status=status, created_at=created_at
    )


class _UnorderedStaging:
    """Returns a batch in an order no reader should rely on."""

    def __init__(self, rows: list[StagedImportRow]) -> None:
        self.rows = rows

    def list_batch(self, batch_id: str) -> list[StagedImportRow]:
        return list(self.rows)


def test_ordinals_follow_created_at_then_id_whatever_order_the_store_returns() -> None:
    staging = _UnorderedStaging([_row("b", _LATE), _row("z", _EARLY, "rejected"), _row("a", _LATE)])

    review = ReviewImport(cast(ImportStagingStore, staging)).execute("batch")

    assert [(row.ordinal, row.id, row.status) for row in review.candidates] == [
        (1, "z", "rejected"),
        (2, "a", "pending"),
        (3, "b", "pending"),
    ]

