"""Changelog inspection and explicit index maintenance CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from people_context.adapters.model2vec_embeddings import (
    MODEL_DOWNLOAD_SIZE,
    MODEL_ID,
    MODEL_URL,
    download_embedding_provider,
    semantic_cache_dir,
)
from people_context.adapters.runtime import ApplicationRuntime
from people_context.adapters.sqlite.semantic import create_sqlite_vector_index
from people_context.app.records import (
    CliAction,
    DoctorError,
    DoctorFinding,
    McpAction,
    render_doctor_json,
)
from people_context.app.semantic import ReindexSemantic
from people_context.app.sync import WatchChangelogError
from people_context.ports.changelog import ChangelogEntry

WATCH_DISCLOSURE_WARNING = (
    "Watch prints full replay payloads, which may contain sensitive personal data. "
    "They go to this terminal only; redirecting them anywhere else is your own disclosure decision."
)

DOCTOR_DISCLOSURE_WARNING = (
    "This report juxtaposes stored personal values, including elevated ones, and is outside the "
    "server's disclosure controls. Inspect it before sharing it anywhere."
)


def cmd_sync_log(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Inspect the local replayable changelog."""
    entries = runtime.changelog.list_entries(limit=args.limit, entity_id=args.entity)
    if not entries:
        print("No changelog entries.")
        return 0
    for entry in entries:
        fields = ",".join(entry.changed_fields) if entry.changed_fields else "-"
        print(
            f"{entry.op_kind}  {entry.entity_type}:{entry.entity_id}  device={entry.device_id}  "
            f"hlc={entry.hlc_physical_ms}:{entry.hlc_logical}  fields={fields}"
        )
        if args.payloads:
            payload = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
            print(f"  payload={payload}")
    return 0


def cmd_watch(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Follow the local changelog, printing one JSON object per new entry."""
    try:
        stream = runtime.use_cases.watch_changelog.stream(
            interval_seconds=args.interval,
            from_start=args.from_start,
        )
    except WatchChangelogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # The notice and the interrupt acknowledgement go to stderr so that stdout stays a
    # clean stream of JSON lines even when it is redirected to a file or another program.
    print(WATCH_DISCLOSURE_WARNING, file=sys.stderr)
    try:
        for entry in stream:
            # Flushed per line: a tail is read as it happens, and a redirected stdout is
            # block-buffered, which would otherwise hold entries back indefinitely.
            print(_render_entry(entry), flush=True)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    except BrokenPipeError:
        # A reader such as `head` closed the pipe. Point the file descriptor at the null
        # device so the interpreter's final flush cannot raise again during shutdown.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _render_entry(entry: ChangelogEntry) -> str:
    """Render one changelog entry as a canonical single-line JSON object."""
    return json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cmd_doctor(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Report deterministic data-quality findings without repairing anything."""
    only = _requested_codes(args.only)
    try:
        report = runtime.use_cases.report_doctor_findings.execute(only=only)
    except DoctorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        # The document is the whole of stdout, so the notice goes to stderr and a redirected
        # report stays byte-identical to the rendered document.
        print(DOCTOR_DISCLOSURE_WARNING, file=sys.stderr)
        print(render_doctor_json(report), end="")
        return 0

    if not report.findings:
        # Nothing was found, so no stored personal value is about to be printed.
        print("No findings.")
        return 0
    # The notice precedes the evidence: a warning that arrives after the values are already on
    # screen cannot inform the decision it exists to inform.
    print(DOCTOR_DISCLOSURE_WARNING)
    print(f"\n{len(report.findings)} finding(s).")
    for finding in report.findings:
        print()
        _print_finding(finding)
    # Findings are a report, not a failure: the exit status says the report completed.
    return 0


def _requested_codes(only: str | None) -> list[str] | None:
    """Split a `--only` value into codes, leaving validation to the use case."""
    if only is None:
        return None
    return [value.strip() for value in only.split(",") if value.strip()]


def _print_finding(finding: DoctorFinding) -> None:
    """Render one finding, including a copyable rendering of each structured action."""
    print(f"[{finding.code}] {finding.message}")
    for person in finding.people:
        marker = " (self)" if person.is_self else ""
        print(f"  person   {person.person_id}  {person.name}{marker}")
    for name in finding.names:
        print(f"  name     {name.person_id}  {name.source}  {name.value!r}")
    for fact in finding.facts:
        period = f"{fact.valid_from or '-'}..{fact.valid_to or '-'}"
        print(f"  fact     {fact.fact_id}  {fact.predicate}={fact.value!r}  [{fact.sensitivity}]  {period}")
    for reference in finding.references:
        print(f"  ref      {reference.entity_type}:{reference.entity_id}")
    for action in finding.actions:
        print(f"  action   {_render_action(action)}")


def _render_action(action: CliAction | McpAction) -> str:
    """Render a structured action as copyable text; the JSON document keeps the structure."""
    if isinstance(action, CliAction):
        return f"cli  {shlex.join(action.argv)}"
    arguments = json.dumps(action.arguments, ensure_ascii=False)
    rendered = f"mcp  {action.tool} {arguments}"
    if not action.requires:
        return rendered
    # Say plainly that this one is a starting point rather than a call ready to run.
    return f"{rendered}  (you supply: {', '.join(action.requires)})"


def cmd_reindex(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Rebuild full-text and optionally semantic indexes."""
    result = runtime.use_cases.reindex_people.execute()
    print(f"Reindexed {result.people} people and {result.names} names.")
    if not args.semantic:
        return 0
    print(f"Semantic model: {MODEL_ID}")
    print(f"Pinned artifact: {MODEL_URL}")
    print(f"Download size: {MODEL_DOWNLOAD_SIZE}")
    print(f"Cache directory: {semantic_cache_dir()}")
    try:
        provider = download_embedding_provider()
        semantic_result = ReindexSemantic(
            runtime.semantic_documents,
            provider,
            create_sqlite_vector_index(runtime.conn),
        ).execute()
    except Exception as exc:  # noqa: BLE001 - preserve prior index on package, download, or embedding failures
        print(f"Semantic reindex failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Reindexed {semantic_result.entities} semantic entities "
        f"({semantic_result.people} people, {semantic_result.interactions} interactions) "
        f"with {semantic_result.model_id}."
    )
    return 0
