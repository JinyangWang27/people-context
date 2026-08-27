"""The size question a bounded boundary must answer before it reads a whole batch.

`ReviewImport` and `CommitImport` materialize an entire staging batch. That is the right
contract for the MCP tools that created those batches, and it is not narrowed here. But the
`pctx import` group can be pointed at a batch staged through the older uncapped
`stage_candidates` path, so it asks this question first: is this batch inside the envelope
this command can render and commit? The answer comes from a bounded aggregate query, before
any candidate body is loaded and before any mutation.
"""

from __future__ import annotations

from typing import Final

from people_context.app.imports.limits import (
    BATCH_TOO_LARGE_FOR_CLI,
    CLI_IMPORT_BUDGET,
    MAX_CLI_STAGED_CANDIDATES,
    ImportBudget,
    resource_limit_error,
)
from people_context.ports.imports import ImportStagingSizeReader, StagedBatchSize

#: A payload-only budget still needs a finite scan; it borrows the row ceiling the CLI uses.
_PAYLOAD_ONLY_SCAN_LIMIT: Final = MAX_CLI_STAGED_CANDIDATES + 1


class PreflightImportBatch:
    """Refuse a staged batch outside the caller's budget before any full-batch read."""

    def __init__(self, sizes: ImportStagingSizeReader, budget: ImportBudget = CLI_IMPORT_BUDGET) -> None:
        self._sizes = sizes
        self._budget = budget

    def execute(self, batch_id: str) -> StagedBatchSize:
        """Return the batch's measured size, or refuse it for being outside the budget.

        An absent batch measures as zero rows and is *not* refused here: whether a batch id
        exists stays the answer of the use case the caller runs next, so an unknown id keeps
        reporting `batch_not_found` rather than a size complaint.
        """
        max_rows = self._budget.max_candidates
        max_payload = self._budget.max_staged_payload_bytes
        if max_rows is None and max_payload is None:
            return StagedBatchSize(row_count=0, payload_bytes=0, truncated=False)

        # One row past the ceiling is all the evidence a refusal needs, so the scan stops
        # there instead of counting a batch it has already decided against.
        scan_limit = max_rows + 1 if max_rows is not None else _PAYLOAD_ONLY_SCAN_LIMIT
        size = self._sizes.measure_batch(batch_id, row_scan_limit=scan_limit)
        if max_rows is not None and (size.truncated or size.row_count > max_rows):
            raise resource_limit_error(
                BATCH_TOO_LARGE_FOR_CLI,
                f"import batch holds more than the {max_rows} candidates this command can read",
                batch_id=batch_id,
                limit=max_rows,
            )
        if max_payload is not None and size.payload_bytes > max_payload:
            raise resource_limit_error(
                BATCH_TOO_LARGE_FOR_CLI,
                f"import batch exceeds the {max_payload} byte reviewable payload this command can read",
                batch_id=batch_id,
                limit=max_payload,
            )
        return size
