"""Database path and export CLI commands."""

from __future__ import annotations

import argparse
import json
import os
import sys

from people_context.adapters.runtime import ApplicationRuntime
from people_context.config import describe_resolution, resolve_db_path
from people_context.ports.vault import VaultSafetyError


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
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
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
