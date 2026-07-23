"""Changelog inspection and explicit index maintenance CLI commands."""

from __future__ import annotations

import argparse
import json
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
