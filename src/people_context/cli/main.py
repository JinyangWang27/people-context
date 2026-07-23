"""Parser dispatch for the human CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.cli.maintenance import cmd_reindex, cmd_sync_log
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
from people_context.cli.portability import cmd_db_path, cmd_export, cmd_export_vault
from people_context.cli.relationships import cmd_normalize_relationships, cmd_relationship_types

CommandHandler = Callable[[ApplicationRuntime, argparse.Namespace], int]

_COMMANDS: dict[str, CommandHandler] = {
    "list": cmd_list,
    "search": cmd_search,
    "show": cmd_show,
    "export": cmd_export,
    "export-vault": cmd_export_vault,
    "edit": cmd_edit,
    "add-alias": cmd_add_alias,
    "set": cmd_set,
    "delete": cmd_delete,
    "relationship-types": cmd_relationship_types,
    "normalize-relationships": cmd_normalize_relationships,
    "sync-log": cmd_sync_log,
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

    runtime = build_runtime(
        args.db,
        warning=lambda message: print(f"Warning: {message}", file=sys.stderr),
    )
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
