"""`pctx sources` and `pctx source show` — local provenance inspection.

These two commands answer one question — where did this come from? — and deliberately stop there.
They are not a document browser: there is no raw source, no path, no extraction configuration, and
no way to retrieve the material a receipt describes, because People Context never stored it.

Both commands are pages rather than dumps. A source with a hundred thousand mappings is traversed
by repeated calls carrying `--cursor`, and no invocation ever holds more than one page. A cursor is
accepted only by the listing that issued it: one from elsewhere is refused rather than reinterpreted
as a boundary here, which would return a page silently missing part of this source's provenance.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.imports import (
    UNKNOWN_SOURCE_SESSION,
    SourceDetailResult,
    SourceInspectionError,
    SourceListResult,
    SourceMappingEntry,
    SourceSummary,
    import_source_document,
    import_sources_document,
    render_import_json,
)
from people_context.cli.rendering import print_table, truncate

#: Width one caller-authored label is rendered at in the human listing. The stored value is
#: bounded already; this keeps a 256-character label from unaligning a table.
_LABEL_DISPLAY_WIDTH = 40

#: Stands in for a field this source does not carry, or that inspection withholds.
_ABSENT = "-"

SOURCES_DISCLOSURE_WARNING = (
    "Import receipts are metadata about personal material: labels and external ids are your own "
    "wording, and a digest identifies a file rather than anonymizing it."
)


def cmd_sources(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """List local import receipts, newest first, one bounded page at a time."""
    try:
        result = runtime.use_cases.list_import_sources.execute(limit=args.limit, cursor=args.cursor)
    except SourceInspectionError as exc:
        return _refuse(exc)
    # The reminder belongs to the disclosure, not to one rendering of it: `--json` carries the
    # same labels, external ids, and file-identifying digests. It goes to stderr, so the promise
    # that stdout holds exactly one document is untouched.
    _warn_if_disclosing(bool(result.sources))
    if args.json:
        print(render_import_json(import_sources_document(result)), end="")
        return 0
    _print_sources(result)
    return 0


def cmd_source(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch one `source` subcommand."""
    handler = _SOURCE_SUBCOMMANDS[args.source_command]
    return handler(runtime, args)


def cmd_source_show(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Show one receipt and a bounded page of what its candidates produced."""
    try:
        result = runtime.use_cases.show_import_source.execute(
            args.source_session_id,
            limit=args.limit,
            cursor=args.cursor,
        )
    except SourceInspectionError as exc:
        return _refuse(exc)
    _warn_if_disclosing(True)
    if args.json:
        print(render_import_json(import_source_document(result)), end="")
        return 0
    _print_source(result)
    return 0


def _warn_if_disclosing(disclosing: bool) -> None:
    """Remind once, on stderr, whenever a result actually carries receipt metadata.

    An empty listing discloses nothing, so it warns about nothing.
    """
    if disclosing:
        print(f"Warning: {SOURCES_DISCLOSURE_WARNING}", file=sys.stderr)


def _print_sources(result: SourceListResult) -> None:
    """Render one listing page and how to continue it."""
    if not result.sources:
        print("No import sources.")
        return
    print_table(
        ["ID", "KIND", "STATUS", "CREATED", "LABEL"],
        [_source_row(source) for source in result.sources],
    )
    if result.next_cursor is not None:
        print(f"\n{_continuation(result.next_cursor)}")


def _print_source(result: SourceDetailResult) -> None:
    """Render one receipt, its aggregate counts, and one page of its outcomes."""
    source = result.source
    print(f"{source.id} ({source.source_kind})")
    print(f"  status: {source.status}")
    print(f"  claim: {_claim_state(source)}")
    if source.redacted:
        # Everything erasure cleared stays cleared, and the fields the row structurally kept —
        # its timestamp above all — are withheld rather than reported.
        print("  This source's records were all forgotten; only its claim is retained.")
        return
    print(f"  created: {_created(source)}")
    print(f"  label: {source.label or _ABSENT}")
    print(f"  external source id: {source.external_source_id or _ABSENT}")
    print(f"  batch: {source.batch_id or _ABSENT}")
    print(f"  committed candidates: {result.counts.mappings_total} ({_pairs(result.counts.mappings_by_disposition)})")
    print(f"  staged candidates: {result.counts.staged_total} ({_pairs(result.counts.staged_by_status)})")
    if not result.mappings:
        print("\nNo committed candidates on this page.")
        return
    print()
    print_table(
        ["CANDIDATE", "OUTCOME", "TYPE", "RECORD"],
        [_mapping_row(mapping) for mapping in result.mappings],
    )
    if result.next_cursor is not None:
        print(f"\n{_continuation(result.next_cursor, subject='candidates')}")


def _source_row(source: SourceSummary) -> tuple[str, str, str, str, str]:
    return (
        source.id,
        source.source_kind,
        source.status,
        _created(source),
        truncate(source.label, _LABEL_DISPLAY_WIDTH) if source.label else _ABSENT,
    )


def _mapping_row(mapping: SourceMappingEntry) -> tuple[str, str, str, str]:
    return (
        mapping.candidate_id,
        mapping.disposition,
        mapping.entity_type,
        # A `merged_away` outcome names no record on purpose: the edge it produced was removed
        # during a person merge and reporting its id would point at nothing.
        mapping.entity_id or _ABSENT,
    )


def _continuation(cursor: str, *, subject: str = "sources") -> str:
    """Say how to reach the next page without pretending to know the whole command.

    A full `pctx ...` line would be wrong the moment the caller used a global option: `--db` and
    `--encrypted` come before the subcommand, so a hint that omitted them would name a command
    that reads a different database — creating and migrating the default one, or failing to open
    an encrypted store — rather than continuing the page just printed. Naming only the argument
    that actually changes is correct under every invocation.
    """
    return f"More {subject}; re-run this command with --cursor {cursor} to continue."


def _created(source: SourceSummary) -> str:
    return _ABSENT if source.created_at is None else source.created_at.isoformat()


def _claim_state(source: SourceSummary) -> str:
    """Describe the duplicate claim without composing the caller a key to reuse."""
    if not source.claimed:
        return "none (this source makes no duplicate-import promise)"
    fingerprint = source.extraction_fingerprint or "absent"
    return f"digest {source.content_digest}, extraction fingerprint {fingerprint}"


def _pairs(counts: dict[str, int]) -> str:
    """Render one aggregate summary in stable key order."""
    return ", ".join(f"{key}: {total}" for key, total in sorted(counts.items())) or "none"


def _refuse(exc: SourceInspectionError) -> int:
    """Report a bounded diagnostic on stderr, leaving stdout free of a partial document.

    An unknown source exits 1 the way an unknown person reference does; a page argument outside
    its range exits 2 the way every other bad CLI parameter does.
    """
    print(f"Error: {exc.code}: {exc}", file=sys.stderr)
    return 1 if exc.code == UNKNOWN_SOURCE_SESSION else 2


_SOURCE_SUBCOMMANDS: dict[str, Callable[[ApplicationRuntime, argparse.Namespace], int]] = {
    "show": cmd_source_show,
}
