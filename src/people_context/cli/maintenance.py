"""Changelog inspection and explicit index maintenance CLI commands."""

from __future__ import annotations

import argparse
import json
import os
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
from people_context.app.semantic import ReindexSemantic
from people_context.app.sync import WatchChangelogError
from people_context.ports.changelog import ChangelogEntry

WATCH_DISCLOSURE_WARNING = (
    "Watch prints full replay payloads, which may contain sensitive personal data. "
    "They go to this terminal only; redirecting them anywhere else is your own disclosure decision."
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
