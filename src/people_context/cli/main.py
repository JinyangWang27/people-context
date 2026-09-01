"""Parser dispatch for the human CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.db import (
    EncryptedDatabaseError,
    UnsafeDatabasePathError,
    inspect_schema,
    latest_schema_version,
)
from people_context.cli.imports import cmd_import
from people_context.cli.insights import cmd_stale, cmd_timeline, cmd_upcoming
from people_context.cli.maintenance import cmd_doctor, cmd_reindex, cmd_stats, cmd_sync_log, cmd_watch
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
    cmd_brief,
    cmd_db_path,
    cmd_export,
    cmd_export_vault,
    cmd_export_vcard,
    cmd_reminders_ics,
    cmd_sync_pull,
    cmd_sync_push,
)
from people_context.cli.relationships import cmd_normalize_relationships, cmd_relationship_types
from people_context.cli.sources import cmd_source, cmd_sources
from people_context.config import MissingDatabaseKeyError, resolve_db_key, resolve_db_path

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
    "timeline": cmd_timeline,
    "upcoming": cmd_upcoming,
    "show": cmd_show,
    "doctor": cmd_doctor,
    "stats": cmd_stats,
    "brief": cmd_brief,
    "export": cmd_export,
    "export-vault": cmd_export_vault,
    "export-vcard": cmd_export_vcard,
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
    "import": cmd_import,
    "sources": cmd_sources,
    "source": cmd_source,
}


def _unreadable_stats_target(args: argparse.Namespace) -> tuple[Path, str] | None:
    """Return the path and reason when opening for `stats` would change the database.

    Every other command tolerates the runtime's bootstrap, because opening a store and then
    answering "No people found" is a true answer either way. `stats` cannot, because its entire
    output *is* a measurement of the store, so anything the bootstrap does becomes reported
    data. `open_db` creates the file, applies pending migrations, switches the journal mode,
    and registers this installation's device row — against a store written by an older release
    that is a schema upgrade, a journal rewrite, and a device the report then counts.

    So the question asked here is not "does the file exist" but "would opening it write". It is
    answered over a read-only connection that creates and migrates nothing, and only `stats`
    asks it: every other command keeps the shared runtime exactly as it is. `:memory:` has no
    file to inspect and carries its own explicit storage state in the report.
    """
    path = resolve_db_path(args.db)
    if str(path) == ":memory:":
        return None
    if not path.exists():
        return path, "no database at"
    key = resolve_db_key() if args.encrypted else None
    stored = inspect_schema(path, key)
    if stored is None:
        return path, "cannot read a database at"
    if stored.version < latest_schema_version():
        return path, "a database that needs a schema upgrade at"
    if not stored.is_people_context:
        # An unrelated SQLite file can carry any `user_version`, including a current-looking
        # one. Opening it would rewrite its journal mode and then fail on a table it never had,
        # so it is refused here — someone else's database is the last thing to write to.
        return path, "not a people-context database at"
    return None


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, dispatch one command, and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "db-path":
        return cmd_db_path(args)
    if args.command == "demo":
        return cmd_demo(args)
    if args.command == "stats":
        try:
            refusal = _unreadable_stats_target(args)
        except (MissingDatabaseKeyError, EncryptedDatabaseError, UnsafeDatabasePathError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if refusal is not None:
            path, reason = refusal
            # The path is named because a mistyped one is the case this catches, and
            # `pctx db-path` already prints the resolved path on request. The report redacts
            # it because the report is a document written to be shared; a refusal is not.
            print(
                f"Error: {reason} {path}. Run `uv run pctx init`, or any other command once "
                "against an existing database, before measuring it.",
                file=sys.stderr,
            )
            return 1

    try:
        runtime = build_runtime(
            args.db,
            warning=lambda message: print(f"Warning: {message}", file=sys.stderr),
            encrypted=args.encrypted,
        )
    except (MissingDatabaseKeyError, EncryptedDatabaseError, UnsafeDatabasePathError) as exc:
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
