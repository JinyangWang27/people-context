"""Database path, export, and sync-bundle CLI commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from people_context.adapters.filesystem.private_file import atomic_write_private_text
from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.exports import SYNC_BUNDLE_FILENAME, render_bundle_json
from people_context.config import describe_resolution, resolve_db_path
from people_context.ports.vault import VaultSafetyError

PLAINTEXT_BUNDLE_WARNING = (
    "This bundle is plaintext personal data, audit history, and replay payloads. "
    "Keep and transport it only on encrypted storage or through an encrypted channel."
)


def cmd_db_path(args: argparse.Namespace) -> int:
    """Print the resolved database path or full trace."""
    if args.verbose:
        for line in describe_resolution(args.db):
            print(line)
    else:
        print(resolve_db_path(args.db))
    return 0


def cmd_export(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Export stable JSON to stdout or an owner-readable file."""
    document = runtime.use_cases.export_data.execute().model_dump(mode="json")
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.output:
        atomic_write_private_text(args.output, text + "\n")
    else:
        print(text)
    return 0


def cmd_sync_push(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Write one complete bootstrap bundle as an owner-only file."""
    document = runtime.use_cases.export_sync_bundle.execute()
    directory = Path(args.output).expanduser()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        destination = atomic_write_private_text(directory / SYNC_BUNDLE_FILENAME, render_bundle_json(document))
    except OSError as exc:
        print(f"Cannot write the sync bundle to {directory}: {exc.strerror or exc}", file=sys.stderr)
        return 1

    snapshot = document.snapshot
    vocabulary = document.relationship_vocabulary
    print(f"Wrote sync bundle to {destination}.")
    print(
        f"People {len(snapshot.people)}, organizations {len(snapshot.organizations)}, "
        f"affiliations {len(snapshot.affiliations)}, relationships {len(snapshot.relationships)}, "
        f"facts {len(snapshot.facts)}, observations {len(snapshot.observations)}, "
        f"traits {len(snapshot.traits)}, interactions {len(snapshot.interactions)}, "
        f"reminders {len(snapshot.reminders)}, preferences {len(snapshot.user_preferences)}, "
        f"audit entries {len(snapshot.audit_log)}."
    )
    print(
        f"Relationship types {len(vocabulary.types)}, synonyms {len(vocabulary.synonyms)}, "
        f"devices {len(document.devices)}, changelog entries {len(document.changelog)}."
    )
    print(
        f"Origin device {document.origin_device_id} at watermark "
        f"{document.watermark.hlc_physical_ms}/{document.watermark.hlc_logical}."
    )
    print(PLAINTEXT_BUNDLE_WARNING)
    return 0


def cmd_export_vault(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Export an Obsidian relationship vault."""
    try:
        result = runtime.use_cases.export_vault.execute(
            args.output,
            include_sensitive=args.include_sensitive,
        )
    except VaultSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Exported {result.people} people and {result.organizations} organizations "
        f"to {result.output} ({result.files} files)."
    )
    return 0
