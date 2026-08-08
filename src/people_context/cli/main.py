"""Parser dispatch for the human CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.db import EncryptedDatabaseError
from people_context.cli.insights import cmd_stale, cmd_upcoming
from people_context.cli.maintenance import cmd_reindex, cmd_sync_log, cmd_watch
from people_context.cli.onboarding import cmd_demo, cmd_init
from people_context.cli.parser import build_parser
from people_context.cli.people import (
    cmd_add_alias,
    cmd_delete,
    cmd_edit,
    cmd_list,
    cmd_search,
    cmd_set,
    cmd_show,
)
from people_context.cli.portability import (
    cmd_db_path,
    cmd_export,
    cmd_export_vault,
    cmd_reminders_ics,
    cmd_sync_pull,
    cmd_sync_push,
)
from people_context.cli.relationships import cmd_normalize_relationships, cmd_relationship_types
from people_context.config import MissingDatabaseKeyError

CommandHandler = Callable[[ApplicationRuntime, argparse.Namespace], int]

_SYNC_SUBCOMMANDS: dict[str, CommandHandler] = {"push": cmd_sync_push, "pull": cmd_sync_pull}


def cmd_sync(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch one `sync` subcommand."""
    handler = _SYNC_SUBCOMMANDS[args.sync_command]
    return handler(runtime, args)


_COMMANDS: dict[str, CommandHandler] = {
    "list": cmd_list,
    "search": cmd_search,
    "stale": cmd_stale,
    "upcoming": cmd_upcoming,
    "show": cmd_show,
    "export": cmd_export,
    "export-vault": cmd_export_vault,
    "reminders-ics": cmd_reminders_ics,
    "edit": cmd_edit,
    "add-alias": cmd_add_alias,
    "set": cmd_set,
    "delete": cmd_delete,
    "relationship-types": cmd_relationship_types,
    "normalize-relationships": cmd_normalize_relationships,
    "sync": cmd_sync,
    "sync-log": cmd_sync_log,
    "watch": cmd_watch,
    "reindex": cmd_reindex,
}


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, dispatch one command, and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "db-path":
        return cmd_db_path(args)
    if args.command == "demo":
        return cmd_demo(args)

    try:
        runtime = build_runtime(
            args.db,
            warning=lambda message: print(f"Warning: {message}", file=sys.stderr),
            encrypted=args.encrypted,
        )
    except (MissingDatabaseKeyError, EncryptedDatabaseError) as exc:
        # Refuse with the reason only; the message never carries key material.
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.command == "init":
            return cmd_init(runtime)
        handler = _COMMANDS.get(args.command)
        if handler is None:
            parser.error(f"unknown command: {args.command}")
            return 2
        return handler(runtime, args)
    finally:
        runtime.close()
