"""`pctx import` — the stage, review, and commit lifecycle at a bounded process boundary.

This module is an adapter, not a second import architecture. Extraction, matching, reference
rewriting, acceptance policy, provenance, audit, and changelog all stay in the application use
cases the MCP tools already drive; what is added here is the process boundary around them: a
finite read budget for the file, a storage-level size check before any batch is materialized,
deterministic human output, and the versioned JSON documents.

The lifecycle keeps its review gate on purpose. A staged batch is durable review state, and that
gate is the product invariant that makes import safe — not a step to collapse into a one-shot
command. `stage-candidates` is a second way *in*, never a second way through: it is the entry
point for an agent that has read unstructured material — a meeting transcript, a call note — in
its own environment and distilled it into strict candidates. What it accepts is candidate JSON,
never the source text, which is what keeps prose interpretation outside this process entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from people_context.adapters.importers.bounded_source import SOURCE_TOO_LARGE, read_source_text
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.imports import (
    CANDIDATE_INPUT_TOO_LARGE,
    CANDIDATE_MODELS,
    CLI_IMPORT_BUDGET,
    INVALID_CANDIDATE_JSON,
    MAX_CLI_CANDIDATE_JSON_BYTES,
    SOURCE_PREVIOUSLY_REDACTED,
    CommitImportResult,
    ImportBatchResult,
    ImportPipelineError,
    ImportReviewResult,
    enforce_extraction_request_limits,
    import_batch_document,
    import_commit_document,
    import_review_document,
    render_import_json,
)
from people_context.cli.rendering import print_import_review

#: Validation failures reported for one refused candidate batch before the listing is truncated.
_MAX_REPORTED_VALIDATION_ERRORS = 10

#: Stands in for a location part the candidate models never declared — an unexpected field key
#: is the caller's own text, so it is named as redacted rather than echoed.
_REDACTED_LOCATION_PART = "(redacted)"

#: Every name the strict candidate models declare: the discriminator values and their fields.
#: A location part outside this set did not come from the schema, so it came from the payload.
_DECLARED_CANDIDATE_NAMES: frozenset[str] = frozenset(CANDIDATE_MODELS) | {
    field for model in CANDIDATE_MODELS.values() for field in model.model_fields
}

#: Pydantic error types whose `msg` is written from the schema rather than from the input.
#:
#: Membership is decided by what a message actually contains, not by which layer produced it —
#: `tests/cli/test_import_stage_candidates.py` probes every entry with a sentinel value and
#: fails if one interpolates it. Two absences are deliberate. `value_error` covers the staging
#: rules, which raise it carrying the person ref that failed. `union_tag_invalid` quotes the
#: rejected discriminator back, so an unsupported `type` — private source text, for instance —
#: would be echoed by the one error whose whole purpose is to report an unrecognized value.
_SCHEMA_DERIVED_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "missing",
        "extra_forbidden",
        "string_type",
        "string_too_short",
        "string_too_long",
        "int_type",
        "float_type",
        "bool_type",
        "list_type",
        "dict_type",
        "datetime_type",
        "datetime_parsing",
        "datetime_from_date_parsing",
        "enum",
        "literal_error",
        "union_tag_not_found",
        "greater_than_equal",
        "less_than_equal",
    }
)

#: How each staging entry point says "yes, process this exact source again anyway".
#:
#: The two commands opt out of the duplicate rule differently, and naming the wrong one is not a
#: cosmetic slip: `--force` is defined only on `import stage`, which owns the file, so offering it
#: to `import stage-candidates` would point a caller at a flag that command does not accept and a
#: workflow it cannot run. `stage-candidates` competes for a canonical claim only because the
#: caller chose to compute `--content-digest` over the artifact themselves; withholding that digest
#: asserts no claim, so that is the same intent expressed at that boundary.
_STAGE_REPROCESS_HINT = "Import it again anyway with: pctx import stage ... --force"
_STAGE_CANDIDATES_REPROCESS_HINT = (
    "Stage these candidates anyway by omitting --content-digest, which asserts no duplicate claim."
)

REVIEW_DISCLOSURE_WARNING = (
    "Review output and the import JSON documents carry distilled personal data from your "
    "export. Inspect them before redirecting or sharing them anywhere."
)


def cmd_import(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch one `import` subcommand."""
    handler = _IMPORT_SUBCOMMANDS[args.import_command]
    return handler(runtime, args)


def cmd_import_stage(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Extract one local export into a reviewable staging batch.

    Re-staging a source this database already imported reports that existing batch instead of
    creating a second copy of the same records. `--force` is the explicit way to say the repeat
    is intentional; it never weakens the duplicate rule, it opts one invocation out of it.
    """
    path = _readable_source(args.path)
    if path is None:
        return 1
    try:
        batch = runtime.use_cases.import_content.execute(
            args.source,
            path=str(path),
            self_sender=args.self_sender,
            budget=CLI_IMPORT_BUDGET,
            label=args.label,
            external_source_id=args.external_source_id,
            forced=args.force,
        )
    except ImportPipelineError as exc:
        if exc.code == SOURCE_PREVIOUSLY_REDACTED:
            # There is deliberately no batch to report: this source's records were hard-forgotten,
            # which removed the batch association along with them. stdout stays empty in both
            # modes because a `--json` caller is promised a document only on success, and a
            # fabricated batch id would be a document about nothing.
            return _refuse(f"{exc.code}: {exc}", hint=_STAGE_REPROCESS_HINT)
        return _refuse(f"import staging failed: {exc}")
    except ImportExtractionError as exc:
        return _refuse(f"import staging failed: {exc}")
    except OSError as exc:
        return _refuse(f"cannot read source file: {exc}")
    if args.json:
        print(render_import_json(import_batch_document(batch)), end="")
        return 0
    _print_batch(batch, duplicate_hint=_STAGE_REPROCESS_HINT)
    return 0


def cmd_import_stage_candidates(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Stage one agent's strict candidate JSON into a reviewable batch.

    The input is what an agent distilled, not what it read. That distinction is the whole design:
    the transcript stays in the agent's environment, and this command never sees it. Everything
    the batch then goes through — matching, the review gate, commit — is the same application
    path `stage_candidates` drives over MCP.
    """
    candidates = _read_candidate_json(args.input)
    if candidates is None:
        return 1
    # `StageCandidates` normalizes the label it stores, so the CLI bounds the same normalized
    # form rather than a padded one that would measure differently here than it does in staging.
    source = args.source.strip()
    try:
        enforce_extraction_request_limits(source, candidates)
        # Every batch here is agent-extracted, whichever candidate types it happens to use, so
        # this boundary demands ambiguity-preserving matching outright rather than inferring it
        # from the vocabulary — for the same reason it applies the extraction limits outright.
        batch = runtime.use_cases.stage_candidates.execute(
            source,
            candidates,
            strict_identity=True,
            source_kind=args.source_kind,
            content_digest=args.content_digest,
            extraction_fingerprint=args.extraction_fingerprint,
            label=args.label,
            external_source_id=args.external_source_id,
        )
    except ImportPipelineError as exc:
        if exc.code == SOURCE_PREVIOUSLY_REDACTED:
            # Same refusal as the file path, and the same silent stdout for the same reason; only
            # the route past it belongs to this command rather than to `import stage`.
            return _refuse(f"{exc.code}: {exc}", hint=_STAGE_CANDIDATES_REPROCESS_HINT)
        _refuse(f"candidate staging failed: {exc}")
        _print_validation_details(exc)
        return 1
    if args.json:
        print(render_import_json(import_batch_document(batch)), end="")
        return 0
    _print_batch(batch, duplicate_hint=_STAGE_CANDIDATES_REPROCESS_HINT)
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


def _print_validation_details(exc: ImportPipelineError) -> None:
    """Say which candidate and field failed, without repeating what was in it.

    An agent that must correct its own candidate JSON needs the location of a failure, but a
    validation error is not automatically safe to print: a rejected extra field puts its own
    untrusted key into `loc`, and an error raised by the staging rules themselves puts the
    offending person ref into `msg`. Both are candidate content, so both are reconstructed here
    from the schema rather than forwarded — a location part is shown only when the models
    actually declare it, and a message only when Pydantic derived it from the schema. Anything
    else degrades to a placeholder or to the error's own fixed type slug, which still names the
    kind of failure. The refusal line above is always payload-independent.

    The listing is truncated because one malformed batch can fail in as many places as it has
    candidates.
    """
    details = exc.details.get("details")
    if not isinstance(details, list):
        return
    for entry in details[:_MAX_REPORTED_VALIDATION_ERRORS]:
        if not isinstance(entry, dict):
            continue
        print(f"  {_safe_location(entry.get('loc'))}: {_safe_message(entry)}", file=sys.stderr)
    remaining = len(details) - _MAX_REPORTED_VALIDATION_ERRORS
    if remaining > 0:
        print(f"  ... and {remaining} more", file=sys.stderr)


def _safe_location(location: Any) -> str:
    """Render a validation location from declared names and indexes only."""
    if not isinstance(location, (list, tuple)) or not location:
        return "(candidate)"
    parts = []
    for part in location:
        if isinstance(part, int):
            parts.append(str(part))
        elif isinstance(part, str) and part in _DECLARED_CANDIDATE_NAMES:
            parts.append(part)
        else:
            parts.append(_REDACTED_LOCATION_PART)
    return ".".join(parts)


def _safe_message(entry: dict[str, Any]) -> str:
    """Return the entry's message only when Pydantic built it from the schema."""
    error_type = entry.get("type")
    if not isinstance(error_type, str):
        return "invalid"
    message = entry.get("msg")
    if error_type in _SCHEMA_DERIVED_ERROR_TYPES and isinstance(message, str):
        return message
    return error_type


def _read_candidate_json(raw_input: str) -> list[Any] | None:
    """Return the candidate array this invocation was given, or None once it has refused it.

    The byte ceiling is spent on the read itself rather than on the parsed result, because the
    point is to never hold an oversized input in the first place — a stdin pipe has no size to
    stat, and a file could grow between the stat and the read. Both paths therefore ask for one
    byte more than the budget and refuse when they get it.

    Refusals name only the limit or the shape. Malformed candidate JSON is untrusted extraction
    output, and a `JSONDecodeError` message quotes the document it failed on, so the decoder's
    own text is deliberately dropped.
    """
    if raw_input == "-":
        raw = sys.stdin.buffer.read(MAX_CLI_CANDIDATE_JSON_BYTES + 1)
        if len(raw) > MAX_CLI_CANDIDATE_JSON_BYTES:
            return _refuse_candidates(_input_too_large())
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _refuse_candidates("candidate input is not valid UTF-8")
    else:
        path = _readable_source(raw_input)
        if path is None:
            return None
        try:
            text = read_source_text(str(path), encoding="utf-8", max_bytes=MAX_CLI_CANDIDATE_JSON_BYTES)
        except ImportExtractionError as exc:
            return _refuse_candidates(_input_too_large() if exc.code == SOURCE_TOO_LARGE else str(exc))
        except OSError as exc:
            return _refuse_candidates(f"cannot read candidate input: {exc}")
    try:
        parsed = json.loads(text)
    except ValueError:
        return _refuse_candidates(f"{INVALID_CANDIDATE_JSON}: candidate input is not valid JSON")
    except RecursionError:
        # The decoder recurses per nesting level, so a few tens of kilobytes of nested arrays
        # exhaust the stack long before the input ceiling is near. Depth is a property of the
        # document, so the refusal is the same one any other unparseable input gets.
        return _refuse_candidates(f"{INVALID_CANDIDATE_JSON}: candidate input is nested too deeply")
    if not isinstance(parsed, list) or not all(isinstance(entry, dict) for entry in parsed):
        return _refuse_candidates(
            f"{INVALID_CANDIDATE_JSON}: candidate input must be a JSON array of candidate objects"
        )
    return parsed


def _input_too_large() -> str:
    return f"{CANDIDATE_INPUT_TOO_LARGE}: candidate input is at most {MAX_CLI_CANDIDATE_JSON_BYTES} bytes"


def _refuse_candidates(message: str) -> list[Any] | None:
    """Refuse the way every other command here does, for a caller that returns a value."""
    _refuse(message)
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


def _print_batch(batch: ImportBatchResult, *, duplicate_hint: str) -> None:
    if batch.duplicate:
        # A committed batch may have had its reviewable rows cleaned up, or may have arrived from
        # a bundle carrying only its durable outcomes. Pointing at review for one of those would
        # name a batch review can no longer find, so the count and the next step follow what the
        # batch still holds.
        held = "candidates" if batch.reviewable else "committed candidates"
        print(
            f"This source was already imported as batch {batch.batch_id} "
            f"with {batch.candidate_count} {held}; nothing new was staged."
        )
        _print_source_session(batch)
        if batch.reviewable:
            print(f"Review it with: pctx import review {batch.batch_id}")
        else:
            print("Its candidates are already committed; there is nothing left to review.")
        print(duplicate_hint)
        return
    print(f"Staged batch {batch.batch_id} with {batch.candidate_count} candidates; nothing is committed yet.")
    _print_source_session(batch)
    if batch.skipped_message_ids:
        print(f"Skipped undated messages with ids: {', '.join(batch.skipped_message_ids)}")
    if batch.skipped_without_id:
        print(f"Skipped undated messages without ids: {batch.skipped_without_id}")
    for card in batch.skipped_cards:
        # `index` is the extractor's one-based position of the card it independently skipped;
        # the reason is a fixed vocabulary, never a fragment of the card itself.
        print(f"Skipped card {card.get('index', '?')}: {card.get('reason', 'unknown')}")
    print(f"Review with: pctx import review {batch.batch_id}")


def _print_source_session(batch: ImportBatchResult) -> None:
    """Name the durable receipt this batch belongs to, when it has one."""
    if batch.source_session_id is not None:
        print(f"Source session: {batch.source_session_id}")


def _print_ids(label: str, ids: list[str]) -> None:
    if ids:
        print(f"  {label}: {', '.join(ids)}")


def _refuse(message: str, *, hint: str | None = None) -> int:
    """Report a bounded diagnostic on stderr, leaving stdout free of a partial document."""
    print(f"Error: {message}", file=sys.stderr)
    if hint is not None:
        print(hint, file=sys.stderr)
    return 1


_IMPORT_SUBCOMMANDS: dict[str, Callable[[ApplicationRuntime, argparse.Namespace], int]] = {
    "stage": cmd_import_stage,
    "stage-candidates": cmd_import_stage_candidates,
    "review": cmd_import_review,
    "commit": cmd_import_commit,
}
