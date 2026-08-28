"""Versioned machine documents for the `pctx import` lifecycle.

These are declared interfaces under the compatibility promise, so each one carries an explicit
`format` and integer `version` and grows only by addition. They are projections of the results
the existing use cases already return — nothing here re-derives, filters, or renames what the
lifecycle decided — and the review document deliberately carries the staged candidate exactly
as stored rather than a lossy CLI vocabulary.

Like every other document this project emits about people, these hold personal data. They are
a machine format, not a sanitized one.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.app.imports.models import CommitImportResult, ImportBatchResult, ImportReviewResult

IMPORT_BATCH_FORMAT: Final = "people-context-import-batch"
IMPORT_BATCH_VERSION: Final = 1

IMPORT_REVIEW_FORMAT: Final = "people-context-import-review"
IMPORT_REVIEW_VERSION: Final = 1

IMPORT_COMMIT_FORMAT: Final = "people-context-import-commit"
IMPORT_COMMIT_VERSION: Final = 1


class ImportBatchDocument(BaseModel):
    """What one `pctx import stage` invocation staged, and what it independently skipped.

    `source_session_id`, `duplicate`, and `reviewable` are additive M18 fields: they carry the
    durable receipt this batch belongs to, whether the canonical claim for the source was already
    owned, and whether the batch still has staged rows to review. A reader that predates them sees
    the same batch fields it always did, so the document stays at version 1 under the additive rule.
    """

    format: str = IMPORT_BATCH_FORMAT
    version: int = IMPORT_BATCH_VERSION
    batch_id: str
    candidate_count: int
    skipped_message_ids: list[str] = Field(default_factory=list)
    skipped_without_id: int = 0
    skipped_cards: list[dict[str, int | str]] = Field(default_factory=list)
    source_session_id: str | None = None
    duplicate: bool = False
    reviewable: bool = True


class ImportReviewCandidateEntry(BaseModel):
    """One staged candidate, carrying the stored review-safe representation unchanged."""

    id: str
    source: str
    status: str
    candidate: dict[str, Any] = Field(default_factory=dict)


class ImportReviewDocument(BaseModel):
    """Every candidate in one batch, in deterministic staging order."""

    format: str = IMPORT_REVIEW_FORMAT
    version: int = IMPORT_REVIEW_VERSION
    batch_id: str
    candidates: list[ImportReviewCandidateEntry] = Field(default_factory=list)


class ImportCommitDocument(BaseModel):
    """The outcome of one explicit commit, by canonical candidate id."""

    format: str = IMPORT_COMMIT_FORMAT
    version: int = IMPORT_COMMIT_VERSION
    batch_id: str
    committed_ids: list[str] = Field(default_factory=list)
    unresolved_ids: list[str] = Field(default_factory=list)
    skipped_ids: list[str] = Field(default_factory=list)


def import_batch_document(result: ImportBatchResult) -> ImportBatchDocument:
    """Project one staging result into its versioned document."""
    return ImportBatchDocument(
        batch_id=result.batch_id,
        candidate_count=result.candidate_count,
        skipped_message_ids=list(result.skipped_message_ids),
        skipped_without_id=result.skipped_without_id,
        skipped_cards=list(result.skipped_cards),
        source_session_id=result.source_session_id,
        duplicate=result.duplicate,
        reviewable=result.reviewable,
    )


def import_review_document(result: ImportReviewResult) -> ImportReviewDocument:
    """Project one review result into its versioned document."""
    return ImportReviewDocument(
        batch_id=result.batch_id,
        candidates=[
            ImportReviewCandidateEntry(
                id=row.id,
                source=row.source,
                status=row.status,
                candidate=row.candidate,
            )
            for row in result.candidates
        ],
    )


def import_commit_document(result: CommitImportResult) -> ImportCommitDocument:
    """Project one commit result into its versioned document."""
    return ImportCommitDocument(
        batch_id=result.batch_id,
        committed_ids=list(result.committed_ids),
        unresolved_ids=list(result.unresolved_ids),
        skipped_ids=list(result.skipped_ids),
    )


def render_import_json(
    document: ImportBatchDocument | ImportReviewDocument | ImportCommitDocument,
) -> str:
    """Render one import document as canonical JSON text."""
    return render_json_document(document)
