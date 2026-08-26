"""`pctx import` — the stage, review, and commit lifecycle at a bounded process boundary.

This module is an adapter, not a second import architecture. Extraction, matching, reference
rewriting, acceptance policy, provenance, audit, and changelog all stay in the application use
cases the MCP tools already drive; what is added here is the process boundary around them: a
finite read budget for the file, a storage-level size check before any batch is materialized,
deterministic human output, and the versioned JSON documents.

The lifecycle stays three commands on purpose. A staged batch is durable review state, and the
review gate is the product invariant that makes import safe — not a step to collapse into a
one-shot command.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.imports import (
    CLI_IMPORT_BUDGET,
    CommitImportResult,
    ImportBatchResult,
    ImportPipelineError,
    ImportReviewResult,
    import_batch_document,
    import_commit_document,
    import_review_document,
    render_import_json,
)
from people_context.cli.rendering import print_import_review

REVIEW_DISCLOSURE_WARNING = (
    "Review output and the import JSON documents carry distilled personal data from your "
    "export. Inspect them before redirecting or sharing them anywhere."
)


def cmd_import(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch one `import` subcommand."""
    handler = _IMPORT_SUBCOMMANDS[args.import_command]
    return handler(runtime, args)


def cmd_import_stage(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Extract one local export into a reviewable staging batch."""
    path = _readable_source(args.path)
    if path is None:
        return 1
    try:
        batch = runtime.use_cases.import_content.execute(
            args.source,
            path=str(path),
            self_sender=args.self_sender,
            budget=CLI_IMPORT_BUDGET,
        )
    except (ImportPipelineError, ImportExtractionError) as exc:
        return _refuse(f"import staging failed: {exc}")
    except OSError as exc:
        return _refuse(f"cannot read source file: {exc}")
    if args.json:
        print(render_import_json(import_batch_document(batch)), end="")
        return 0
    _print_batch(batch)
    return 0


def cmd_import_review(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Show every staged candidate in one batch."""
    review = _bounded_review(runtime, args.batch_id)
    if isinstance(review, int):
        return review
    if args.json:
        print(render_import_json(import_review_document(review)), end="")
        return 0
    print(f"Warning: {REVIEW_DISCLOSURE_WARNING}", file=sys.stderr)
    print(f"Batch {review.batch_id}: {len(review.candidates)} candidates.")
    print_import_review(review.candidates)
    print(f"Commit with: pctx import commit {review.batch_id} --all")
    return 0


def cmd_import_commit(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Commit the explicitly accepted candidates of one batch.

    `--all` and `--accept` are both explicit approval, so neither asks a second time. `--all`
    reads the batch through the same review use case the operator would have run, which is
    what keeps the selection the batch's own candidate ids rather than a wildcard the commit
    use case would have to interpret.
    """
    if args.all:
        review = _bounded_review(runtime, args.batch_id)
        if isinstance(review, int):
            return review
        accepted_ids = [row.id for row in review.candidates]
    else:
        preflight = _preflight(runtime, args.batch_id)
        if preflight is not None:
            return preflight
        accepted_ids = list(dict.fromkeys(args.accept))
    try:
        result = runtime.use_cases.commit_import.execute(args.batch_id, accepted_ids)
    except ImportPipelineError as exc:
        return _refuse(f"import commit failed: {exc}")
    if args.json:
        print(render_import_json(import_commit_document(result)), end="")
        return 0
    print_import_commit(result)
    return 0


def parse_candidate_selection(raw: str, known_ids: set[str]) -> list[str] | None:
    """Return the deduplicated canonical ids an operator typed, or None when any is unknown.

    Onboarding and `pctx import` accept candidates the same way, so they reject them the same
    way too: only ids the batch actually staged are selectable, and a typo refuses the whole
    selection rather than silently committing the part that happened to parse.
    """
    typed = [candidate_id.strip() for candidate_id in raw.split(",")]
    accepted_ids = list(dict.fromkeys(candidate_id for candidate_id in typed if candidate_id))
    unknown_ids = sorted(set(accepted_ids) - known_ids)
    if unknown_ids:
        print("Unknown candidate IDs: " + ", ".join(unknown_ids), file=sys.stderr)
        return None
    return accepted_ids


def print_import_commit(result: CommitImportResult) -> None:
    """Report one commit's outcome by canonical candidate id."""
    print(
        f"Committed {len(result.committed_ids)} candidates; "
        f"{len(result.unresolved_ids)} unresolved, {len(result.skipped_ids)} already committed."
    )
    _print_ids("committed", result.committed_ids)
    _print_ids("unresolved", result.unresolved_ids)
    _print_ids("already committed", result.skipped_ids)


def _bounded_review(runtime: ApplicationRuntime, batch_id: str) -> ImportReviewResult | int:
    """Return one batch's review rows, or the exit code that refused to materialize them."""
    refusal = _preflight(runtime, batch_id)
    if refusal is not None:
        return refusal
    try:
        return runtime.use_cases.review_import.execute(batch_id)
    except ImportPipelineError as exc:
        return _refuse(f"import review failed: {exc}")


def _preflight(runtime: ApplicationRuntime, batch_id: str) -> int | None:
    """Refuse a batch outside this command's read envelope before anything materializes it."""
    try:
        runtime.use_cases.preflight_import_batch.execute(batch_id)
    except ImportPipelineError as exc:
        return _refuse(f"import batch cannot be read by this command: {exc}")
    return None


def _readable_source(raw_path: str) -> Path | None:
    """Return the source path only when it is a readable file, refusing safely otherwise."""
    path = Path(raw_path).expanduser().absolute()
    try:
        if not path.is_file():
            raise OSError("path is not a readable file")
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        _refuse(f"cannot read source file: {exc}")
        return None
    return path


def _print_batch(batch: ImportBatchResult) -> None:
    print(f"Staged batch {batch.batch_id} with {batch.candidate_count} candidates; nothing is committed yet.")
    if batch.skipped_message_ids:
        print(f"Skipped undated messages with ids: {', '.join(batch.skipped_message_ids)}")
    if batch.skipped_without_id:
        print(f"Skipped undated messages without ids: {batch.skipped_without_id}")
    for card in batch.skipped_cards:
        print(f"Skipped card {card.get('card', '?')}: {card.get('reason', 'unknown')}")
    print(f"Review with: pctx import review {batch.batch_id}")


def _print_ids(label: str, ids: list[str]) -> None:
    if ids:
        print(f"  {label}: {', '.join(ids)}")


def _refuse(message: str) -> int:
    """Report a bounded diagnostic on stderr, leaving stdout free of a partial document."""
    print(f"Error: {message}", file=sys.stderr)
    return 1


_IMPORT_SUBCOMMANDS: dict[str, Callable[[ApplicationRuntime, argparse.Namespace], int]] = {
    "stage": cmd_import_stage,
    "review": cmd_import_review,
    "commit": cmd_import_commit,
}
