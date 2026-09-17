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
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from people_context.adapters.filesystem.private_file import atomic_write_private_text
from people_context.adapters.importers.bounded_source import SOURCE_TOO_LARGE, read_source_text
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.imports import (
    CANDIDATE_INPUT_TOO_LARGE,
    CANDIDATE_MODELS,
    CLI_IMPORT_BUDGET,
    INVALID_CANDIDATE_JSON,
    INVALID_REVIEW_DOCUMENT,
    MAX_CLI_CANDIDATE_JSON_BYTES,
    SOURCE_PREVIOUSLY_REDACTED,
    CommitImportResult,
    ImportBatchResult,
    ImportPipelineError,
    ImportReviewResult,
    ImportReviewRow,
    edited_document_read_bound,
    enforce_extraction_request_limits,
    import_batch_document,
    import_commit_document,
    import_review_document,
    render_import_json,
    review_document_edits,
)
from people_context.cli.rendering import import_review_lines, import_review_summary, print_import_review
from people_context.domain.import_provenance import STAGING_STATUS_PENDING, STAGING_STATUS_REJECTED
from people_context.ports.sources import STATUS_COMMITTED, STATUS_WITHDRAWN

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
    if args.interactive:
        return _review_interactively(runtime, review)
    print(f"Commit with: pctx import commit {review.batch_id} --all")
    return 0


_T = TypeVar("_T")

#: The refusal an action gets when the batch moved since the review it was based on.
_BATCH_CHANGED = "batch_changed"

#: A selection token that is ordinal shorthand rather than, necessarily, a candidate id.
_ORDINAL_SELECTION = re.compile(r"(\d+)(?:-(\d+))?")


class _BatchChanged(Exception):
    """Another client moved the batch the interactive loop is showing."""


_INTERACTIVE_PROMPT = "[a]ccept [s]kip [w]ithdraw [e]dit [q]uit: "


def _review_interactively(runtime: ApplicationRuntime, review: ImportReviewResult) -> int:
    """Walk the pending candidates one at a time, then commit exactly what was accepted.

    The loop holds the `batch_digest` of the review it is showing and checks every step against
    it: withdraw, amend, and the final commit pass it to the use case, and accept and skip, which
    write nothing, reread first. When another client changed the batch, every acceptance collected
    so far is discarded and the loop starts over on the reread batch — keeping them would let the
    final commit match the new digest while committing a row whose new content the reviewer never
    saw. `q` commits nothing; a skipped row stays pending for a later pass.
    """
    batch_id = review.batch_id
    accepted: list[str] = []
    seen: set[str] = set()
    while True:
        try:
            row = next((r for r in review.candidates if r.status == STAGING_STATUS_PENDING and r.id not in seen), None)
            if row is None:
                if not accepted:
                    print("No candidates accepted; nothing committed.")
                    return 0
                result = _interactive_step(
                    partial(
                        runtime.use_cases.commit_import.execute,
                        batch_id,
                        accepted,
                        expected_batch_digest=review.batch_digest,
                    )
                )
                if result is None:
                    return 1
                print_import_commit(result)
                return 0
            action = _ask(f"{import_review_lines(review.candidates)[row.ordinal - 1]}\n{_INTERACTIVE_PROMPT}")
            if action is None or action.casefold() == "q":
                print("Quit; nothing committed.")
                return 0
            action = action.casefold()
            if action not in {"a", "s", "w", "e"}:
                continue
            current = _bounded_review(runtime, batch_id)
            if isinstance(current, int):
                return current
            if current.batch_digest != review.batch_digest:
                raise _BatchChanged
            revised = _interactive_action(runtime, review, row.id, action)
            if action in {"a", "s"}:
                seen.add(row.id)
                if action == "a":
                    accepted.append(row.id)
            elif revised is not None:
                review = revised
                # An amended row stays unseen, so the loop shows it again for a decision.
                if action == "w":
                    seen.add(row.id)
        except _BatchChanged:
            print(
                f"Batch changed elsewhere; discarded {len(accepted)} accepted candidates, starting over.",
                file=sys.stderr,
            )
            current = _bounded_review(runtime, batch_id)
            if isinstance(current, int):
                return current
            review = current
            accepted.clear()
            seen.clear()


def _interactive_action(
    runtime: ApplicationRuntime, review: ImportReviewResult, candidate_id: str, action: str
) -> ImportReviewResult | None:
    """Run a withdraw or edit against the digest shown; None means nothing was written."""
    batch_id, digest = review.batch_id, review.batch_digest
    if action == "w":
        return _interactive_step(
            partial(
                runtime.use_cases.withdraw_staged_candidates.execute,
                batch_id,
                [candidate_id],
                expected_batch_digest=digest,
            )
        )
    if action != "e":
        return None
    raw_patch = _ask("Patch JSON: ")
    patch = _read_patch_json(raw_patch) if raw_patch is not None else None
    if patch is None:
        return None
    return _interactive_step(
        partial(
            runtime.use_cases.amend_staged_candidate.execute,
            batch_id,
            candidate_id,
            patch,
            expected_batch_digest=digest,
        )
    )


def _ask(prompt: str) -> str | None:
    """Read one answer, treating end of input as a request to stop."""
    try:
        return input(prompt).strip()
    except EOFError:
        return None


def _interactive_step(call: Callable[[], _T]) -> _T | None:
    """Run one write of the interactive loop; a refusal is reported and returns None."""
    try:
        return call()
    except ImportPipelineError as exc:
        if exc.code == _BATCH_CHANGED:
            raise _BatchChanged from exc
        _refuse(f"import review step failed: {exc}")
        _print_validation_details(exc)
        return None


def cmd_import_amend(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Correct one staged candidate in place, then show the batch as it now stands.

    The patch replaces the fields it names and leaves the rest alone. Nothing is committed here:
    an amendment is the reviewer finishing the extraction, not accepting it.
    """
    patch = _read_patch_json(args.patch)
    if patch is None:
        return 1
    refusal = _preflight(runtime, args.batch_id)
    if refusal is not None:
        return refusal
    try:
        review = runtime.use_cases.amend_staged_candidate.execute(args.batch_id, args.candidate_id, patch)
    except ImportPipelineError as exc:
        _refuse(f"import amend failed: {exc}")
        _print_validation_details(exc)
        return 1
    return _print_revised_batch(review, args.json, f"Amended {args.candidate_id}.")


def cmd_import_reject(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Withdraw staged candidates, then show the batch as it now stands.

    A withdrawn candidate is not deleted. It stays listed as `rejected`, so the operator can still
    check that they dropped the right thing, and commit never touches it again.
    """
    candidate_ids = list(dict.fromkeys(args.candidate_id))
    refusal = _preflight(runtime, args.batch_id)
    if refusal is not None:
        return refusal
    try:
        review = runtime.use_cases.withdraw_staged_candidates.execute(args.batch_id, candidate_ids)
    except ImportPipelineError as exc:
        _refuse(f"import reject failed: {exc}")
        _print_validation_details(exc)
        return 1
    return _print_revised_batch(review, args.json, f"Withdrew {len(candidate_ids)} candidates.")


def _print_revised_batch(review: ImportReviewResult, as_json: bool, summary: str) -> int:
    """Print the whole batch after an edit, in the shape `pctx import review` would print it.

    The whole document, not the row that changed. `people-context-import-review` defines
    `candidates` as every candidate in the batch, so emitting a subset under the same format would
    silently repurpose an absent row from "not in this batch" to "not affected by this command".
    """
    if as_json:
        print(render_import_json(import_review_document(review)), end="")
        return 0
    print(f"Warning: {REVIEW_DISCLOSURE_WARNING}", file=sys.stderr)
    print(summary)
    print(f"Batch {review.batch_id}: {len(review.candidates)} candidates.")
    print_import_review(review.candidates)
    print(f"Commit with: pctx import commit {review.batch_id} --all")
    return 0


#: Environment variables naming the operator's editor, in the order they are consulted.
_EDITOR_VARIABLES = ("VISUAL", "EDITOR")

#: Exit status for an edit that cannot start because no editor is configured.
_NO_EDITOR_EXIT = 2


def cmd_import_edit(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Edit a batch's review document, then apply every change it expresses, or none of them.

    Only a row's `candidate` is editable. A deleted row is withdrawn, a changed candidate is
    amended, and an untouched row is a no-op, all through one write-locked use case that checks
    the document's `batch_digest` first. `--from` applies a document edited elsewhere and never
    prompts, because a stdin document leaves nothing to read a confirmation from.
    """
    batch_id = args.batch_id
    try:
        size = runtime.use_cases.preflight_import_batch.execute(batch_id)
    except ImportPipelineError as exc:
        return _refuse(f"import batch cannot be read by this command: {exc}")
    review = _bounded_review(runtime, batch_id)
    if isinstance(review, int):
        return review
    document = import_review_document(review)
    rendered_text = render_import_json(document)
    limit = CLI_IMPORT_BUDGET.max_staged_payload_bytes
    bound = edited_document_read_bound(
        len(rendered_text.encode("utf-8")), size.payload_bytes, limit if limit is not None else 0
    )
    if args.from_file is None:
        editor = _configured_editor()
        if editor is None:
            print(
                f"Error: no editor configured; set {' or '.join(_EDITOR_VARIABLES)} (checked both)",
                file=sys.stderr,
            )
            return _NO_EDITOR_EXIT
        print(f"Warning: {REVIEW_DISCLOSURE_WARNING}", file=sys.stderr)
        edited_text = _edit_in_editor(editor, rendered_text, bound)
    else:
        edited_text = _read_edited_document(args.from_file, bound)
    if edited_text is None:
        return 1
    try:
        edited = json.loads(edited_text)
    except ValueError:
        return _refuse(f"{INVALID_REVIEW_DOCUMENT}: the edited review document is not valid JSON")
    except RecursionError:
        return _refuse(f"{INVALID_REVIEW_DOCUMENT}: the edited review document is nested too deeply")
    try:
        edits = review_document_edits(document.model_dump(mode="json"), edited)
        revised = runtime.use_cases.apply_review_edits.execute(
            batch_id,
            edits.amendments,
            edits.withdrawals,
            expected_batch_digest=review.batch_digest,
        )
    except ImportPipelineError as exc:
        _refuse(f"import edit failed: {exc.code}: {exc}")
        _print_edit_locator(exc, edited)
        _print_validation_details(exc)
        return 1
    print(f"Applied {len(edits.amendments)} amendments and {len(edits.withdrawals)} withdrawals.")
    print(import_review_summary(revised.candidates))
    pending = [row.id for row in revised.candidates if row.status == STAGING_STATUS_PENDING]
    if args.from_file is not None or args.no_commit or not pending:
        if pending:
            print(f"Commit with: pctx import commit {batch_id} --all")
        return 0
    if not _confirm_on_terminal(f"Commit {len(pending)} pending candidates? [y/N] "):
        print(f"Nothing committed. Commit with: pctx import commit {batch_id} --all")
        return 0
    try:
        result = runtime.use_cases.commit_import.execute(
            batch_id, pending, expected_batch_digest=revised.batch_digest
        )
    except ImportPipelineError as exc:
        return _refuse(f"import commit failed: {exc.code}: {exc}")
    print_import_commit(result)
    return 0


def _configured_editor() -> list[str] | None:
    """Return the editor command from `$VISUAL`, then `$EDITOR`, split without a shell."""
    for variable in _EDITOR_VARIABLES:
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        try:
            argv = shlex.split(value)
        except ValueError:
            continue
        if argv:
            return argv
    return None


def _edit_in_editor(editor: list[str], rendered_text: str, bound: int) -> str | None:
    """Open the rendered document in the editor and return what it saved, or None once refused.

    The file is owner-only, lives in a private temporary directory, and is removed in every case
    as soon as the editor's result is read. The exit status is checked before anything is read:
    `subprocess.run` does not raise on a nonzero one, and a crashed editor must never lead to a
    commit prompt over a batch nobody finished reviewing.
    """
    directory = Path(tempfile.mkdtemp(prefix="pctx-import-edit-"))
    try:
        path = atomic_write_private_text(directory / "review.json", rendered_text)
        try:
            completed = subprocess.run([*editor, str(path)], check=False)
        except OSError as exc:
            _refuse(f"cannot start editor: {exc.strerror or exc.__class__.__name__}; nothing applied")
            return None
        if completed.returncode != 0:
            _refuse(f"editor exited with status {completed.returncode}; nothing applied")
            return None
        return _read_edited_document(str(path), bound)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _read_edited_document(raw_input: str, bound: int) -> str | None:
    """Return an edited review document read under the batch-derived bound, or None once refused."""
    too_large = f"{INVALID_REVIEW_DOCUMENT}: the edited review document grew past what this batch can hold"
    if raw_input == "-":
        raw = sys.stdin.buffer.read(bound + 1)
        if len(raw) > bound:
            _refuse(too_large)
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            _refuse(f"{INVALID_REVIEW_DOCUMENT}: the edited review document is not valid UTF-8")
            return None
    path = _readable_source(raw_input)
    if path is None:
        return None
    try:
        return read_source_text(str(path), encoding="utf-8", max_bytes=bound)
    except ImportExtractionError as exc:
        _refuse(too_large if exc.code == SOURCE_TOO_LARGE else f"cannot read review document: {exc}")
        return None
    except OSError as exc:
        _refuse(f"cannot read review document: {exc}")
        return None


def _print_edit_locator(exc: ImportPipelineError, edited: object) -> None:
    """Name the document row a use-case refusal is about, by index and ordinal, never by content."""
    candidate_ids = exc.details.get("candidate_ids") or [exc.details.get("candidate_id")]
    rows = edited.get("candidates") if isinstance(edited, dict) else None
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows):
        if isinstance(row, dict) and row.get("id") in candidate_ids:
            print(f"  at candidates[{index}] (#{row.get('ordinal')})", file=sys.stderr)


def _confirm_on_terminal(prompt: str) -> bool:
    """Ask on the controlling terminal, never on a pipe; no terminal means no."""
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(prompt)
            tty.flush()
            answer = tty.readline()
    except OSError:
        return False
    return answer.strip().casefold() in {"y", "yes"}


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
        # Withdrawing a candidate *was* the instruction, so `--all` leaves it out silently rather
        # than refusing; naming one explicitly in `--accept` is the case that refuses.
        accepted_ids = [row.id for row in review.candidates if row.status != STAGING_STATUS_REJECTED]
    elif _needs_review_to_select(args.accept):
        review = _bounded_review(runtime, args.batch_id)
        if isinstance(review, int):
            return review
        selection = parse_candidate_selection(args.accept, review.candidates)
        if selection is None:
            return 1
        accepted_ids = selection
    else:
        # Plain ids resolve without reading the batch, exactly as they always have.
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


def parse_candidate_selection(tokens: list[str], rows: list[ImportReviewRow]) -> list[str] | None:
    """Return the deduplicated canonical ids an operator selected, or None when any is unknown.

    A selection mixes `#n` ordinals, `a-b` ranges, and canonical ids, comma-separated or given as
    separate tokens. A token or comma-separated part that exactly equals a candidate id is that id
    before any shorthand parsing: staging ids are format-opaque, so a restored batch may hold an id
    spelled `1`, and an invocation naming it keeps its meaning. Onboarding and `pctx import` share
    this parser, so they reject the same way too — one unknown member refuses the whole selection
    rather than silently committing the part that happened to parse.
    """
    known_ids = {row.id for row in rows}
    by_ordinal = {row.ordinal: row.id for row in rows}
    selected: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        parts = [token] if token.strip() in known_ids else token.split(",")
        for raw_part in parts:
            part = raw_part.strip()
            if not part:
                continue
            if part in known_ids:
                selected.append(part)
                continue
            shorthand = _ORDINAL_SELECTION.fullmatch(part)
            if shorthand is None:
                unknown.append(part)
                continue
            first = int(shorthand.group(1))
            last = int(shorthand.group(2) or first)
            ordinals = range(first, last + 1)
            if first > last or any(ordinal not in by_ordinal for ordinal in ordinals):
                unknown.append(part)
                continue
            selected.extend(by_ordinal[ordinal] for ordinal in ordinals)
    if unknown:
        print("Unknown candidate IDs: " + ", ".join(sorted(set(unknown))), file=sys.stderr)
        return None
    return list(dict.fromkeys(selected))


def _needs_review_to_select(tokens: list[str]) -> bool:
    """Whether a selection holds anything a plain list of candidate ids would not."""
    return any("," in token or _ORDINAL_SELECTION.fullmatch(token.strip()) for token in tokens)


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
        # The ceiling is this command's, passed per call: `review_import` keeps the unbounded read
        # contract it shipped with, and one instance of the use case serves both boundaries.
        return runtime.use_cases.review_import.execute(batch_id, budget=CLI_IMPORT_BUDGET)
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


def _read_patch_json(raw_input: str) -> dict[str, Any] | None:
    """Return the amendment patch this invocation was given, or None once it has refused it.

    Bounded on the read for the same reason candidate input is: a stdin pipe has no size to stat,
    so the ceiling is spent before anything is held. Refusals name the limit or the shape and
    never the document, because a patch is untrusted text exactly as a candidate is.
    """
    if raw_input == "-":
        raw = sys.stdin.buffer.read(MAX_CLI_CANDIDATE_JSON_BYTES + 1)
        if len(raw) > MAX_CLI_CANDIDATE_JSON_BYTES:
            _refuse(_input_too_large())
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            _refuse("patch input is not valid UTF-8")
            return None
    else:
        text = raw_input
        if len(text.encode("utf-8")) > MAX_CLI_CANDIDATE_JSON_BYTES:
            _refuse(_input_too_large())
            return None
    try:
        parsed = json.loads(text)
    except ValueError:
        _refuse(f"{INVALID_CANDIDATE_JSON}: patch is not valid JSON")
        return None
    except RecursionError:
        _refuse(f"{INVALID_CANDIDATE_JSON}: patch is nested too deeply")
        return None
    if not isinstance(parsed, dict):
        _refuse(f"{INVALID_CANDIDATE_JSON}: patch must be a JSON object of candidate fields")
        return None
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
        # A batch with nothing left to review may have had its rows cleaned up, may have arrived
        # from a bundle carrying only its durable outcomes, or may have been withdrawn entirely.
        # Pointing at review for any of those would name a batch review can no longer find, and
        # calling a withdrawn one committed would claim durable records it never produced — so
        # the count and the next step follow the receipt's own status.
        held, outcome = _duplicate_wording(batch)
        print(
            f"This source was already imported as batch {batch.batch_id} "
            f"with {batch.candidate_count} {held}; nothing new was staged."
        )
        _print_source_session(batch)
        print(outcome if outcome is not None else f"Review it with: pctx import review {batch.batch_id}")
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


def _duplicate_wording(batch: ImportBatchResult) -> tuple[str, str | None]:
    """Return what the already-imported batch holds, and what is left to do with it.

    A `None` outcome means the batch is still reviewable and the caller should point at review.
    """
    if batch.reviewable:
        return "candidates", None
    if batch.source_status == STATUS_WITHDRAWN:
        return "withdrawn candidates", "Its candidates were all withdrawn; there is nothing left to review."
    if batch.source_status == STATUS_COMMITTED:
        return "committed candidates", "Its candidates are already committed; there is nothing left to review."
    return "candidates", "There is nothing left to review."


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
    "amend": cmd_import_amend,
    "reject": cmd_import_reject,
    "edit": cmd_import_edit,
    "commit": cmd_import_commit,
}
