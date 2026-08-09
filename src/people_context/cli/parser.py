"""Argument parser for the human CLI."""

from __future__ import annotations

import argparse

from people_context.app.insights import (
    DEFAULT_STALE_LIMIT,
    DEFAULT_THRESHOLD_DAYS,
    DEFAULT_WINDOW_DAYS,
    MAX_STALE_LIMIT,
    MAX_THRESHOLD_DAYS,
    MAX_WINDOW_DAYS,
    MIN_STALE_LIMIT,
    MIN_THRESHOLD_DAYS,
    MIN_WINDOW_DAYS,
)
from people_context.app.sync import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
)
from people_context.domain.person import AliasKind


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="pctx", description="Inspect and search your people data.")
    parser.add_argument("--db", default=None, help="Explicit database path, overriding other resolution sources.")
    parser.add_argument(
        "--encrypted",
        action="store_true",
        help=(
            "Open the database with SQLCipher using the PEOPLE_CONTEXT_DB_KEY environment variable; "
            "refuses to run without a non-empty key."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_path = subparsers.add_parser("db-path", help="Print the resolved database path.")
    db_path.add_argument("-v", "--verbose", action="store_true", help="Show the full resolution trace.")

    list_cmd = subparsers.add_parser("list", help="List known people.")
    list_cmd.add_argument("--all", action="store_true", help="Include soft-deleted people.")
    list_cmd.add_argument("--limit", type=int, default=None, help="Maximum number of people to list.")
    list_cmd.add_argument(
        "--json",
        action="store_true",
        help="Print the versioned person-index JSON document instead of the table.",
    )

    brief = subparsers.add_parser("brief", help="Compose one person's deterministic brief.")
    brief.add_argument("person", help="A person id, or a name to resolve.")
    brief.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Widen the context records to sensitive and restricted ones; guidance stays ordinary.",
    )
    brief.add_argument(
        "--json",
        action="store_true",
        help="Print the versioned brief JSON document instead of Markdown.",
    )
    brief.add_argument("--output", default=None, help="Write to this owner-only file instead of stdout.")

    search = subparsers.add_parser("search", help="Ranked search results for a name query.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10, help="Maximum number of results.")

    show = subparsers.add_parser("show", help="Show a person's full record.")
    show.add_argument("person", help="A person id, or a name to resolve.")

    stale = subparsers.add_parser("stale", help="Report people you have not interacted with recently.")
    stale.add_argument("--category", default=None, help="Only people with this relationship-to-self category.")
    stale.add_argument(
        "--threshold-days",
        type=int,
        default=DEFAULT_THRESHOLD_DAYS,
        help=f"Minimum days since the last ordinary interaction ({MIN_THRESHOLD_DAYS}..{MAX_THRESHOLD_DAYS}).",
    )
    stale.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_STALE_LIMIT,
        help=f"Maximum number of reported people ({MIN_STALE_LIMIT}..{MAX_STALE_LIMIT}).",
    )

    upcoming = subparsers.add_parser("upcoming", help="Report birthdays and dated reminders coming up.")
    upcoming.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"Inclusive number of days ahead to report ({MIN_WINDOW_DAYS}..{MAX_WINDOW_DAYS}).",
    )
    upcoming.add_argument("--person", default=None, help="Only this person; an id, or a name to resolve.")

    export = subparsers.add_parser("export", help="JSON dump of all people.")
    export.add_argument("--output", default=None, help="Write to this file instead of stdout.")

    export_vault = subparsers.add_parser("export-vault", help="Export an Obsidian relationship vault.")
    export_vault.add_argument("--output", required=True, help="Empty or marker-owned output directory.")
    export_vault.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include sensitive and restricted facts in files outside server disclosure controls.",
    )

    reminders_ics = subparsers.add_parser(
        "reminders-ics",
        help="Export active dated reminders as an owner-only iCalendar file.",
    )
    reminders_ics.add_argument(
        "--output",
        required=True,
        help="Destination .ics file; an existing file is atomically replaced.",
    )

    edit = subparsers.add_parser("edit", help="Edit a person's canonical name or summary.")
    edit.add_argument("person", help="An active person id, or a name to resolve.")
    edit.add_argument("--name", default=None, help="New canonical name.")
    edit.add_argument("--summary", default=None, help="New summary.")

    add_alias = subparsers.add_parser("add-alias", help="Add an alias to a person.")
    add_alias.add_argument("person", help="An active person id, or a name to resolve.")
    add_alias.add_argument("value")
    add_alias.add_argument("--kind", choices=[kind.value for kind in AliasKind], default=AliasKind.OTHER.value)
    add_alias.add_argument("--lang", default=None)
    add_alias.add_argument("--script", default=None)

    set_cmd = subparsers.add_parser("set", help="Set a supported user preference.")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")

    delete = subparsers.add_parser("delete", help="Permanently forget a person and their linked data.")
    delete.add_argument("person", help="An active person id, or a name to resolve.")
    delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    relationship_types = subparsers.add_parser(
        "relationship-types",
        help="List relationship vocabulary or add a custom type.",
    )
    relationship_type_subcommands = relationship_types.add_subparsers(dest="relationship_types_command")
    relationship_type_add = relationship_type_subcommands.add_parser("add", help="Add custom vocabulary.")
    relationship_type_add.add_argument("type")
    relationship_type_add.add_argument("--category", required=True)
    direction = relationship_type_add.add_mutually_exclusive_group()
    direction.add_argument("--inverse", default=None)
    direction.add_argument("--symmetric", action="store_true")
    relationship_type_add.add_argument(
        "--synonym",
        action="append",
        default=[],
        help="Additional synonym; repeat for multiple values.",
    )

    normalize_relationships = subparsers.add_parser(
        "normalize-relationships",
        help="Preview or apply canonical rewrites to existing relationships.",
    )
    normalize_relationships.add_argument("--apply", action="store_true", help="Execute the reported rewrites.")

    sync = subparsers.add_parser("sync", help="Move this database to another device with a bootstrap bundle.")
    sync_subcommands = sync.add_subparsers(dest="sync_command", required=True)
    sync_push = sync_subcommands.add_parser(
        "push",
        help="Write one complete plaintext bootstrap bundle; store or transport it only when encrypted.",
    )
    sync_push.add_argument(
        "--output",
        required=True,
        help="Directory that receives the owner-only people-context-sync-bundle.json file.",
    )
    sync_pull = sync_subcommands.add_parser(
        "pull",
        help="Restore one bootstrap bundle into a freshly initialized, otherwise untouched database.",
    )
    sync_pull.add_argument(
        "--input",
        required=True,
        help="The bundle file, or a directory containing people-context-sync-bundle.json.",
    )
    sync_pull.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    sync_log = subparsers.add_parser("sync-log", help="Inspect the local replayable changelog.")
    sync_log.add_argument("--limit", type=int, default=50, help="Maximum number of recent entries.")
    sync_log.add_argument("--entity", default=None, help="Filter by exact entity id.")
    sync_log.add_argument(
        "--payloads",
        action="store_true",
        help="Include full replay payloads; hidden by default because they may contain sensitive data.",
    )

    watch = subparsers.add_parser(
        "watch",
        help="Follow the local changelog, printing one JSON object per new entry.",
    )
    watch.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between polls ({MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS}).",
    )
    watch.add_argument(
        "--from-start",
        action="store_true",
        help="Replay every existing entry before following new ones.",
    )

    reindex = subparsers.add_parser("reindex", help="Rebuild active-person full-text search rows.")
    reindex.add_argument(
        "--semantic",
        action="store_true",
        help="Explicitly download/cache the pinned multilingual model and atomically rebuild semantic vectors.",
    )

    subparsers.add_parser("init", help="Interactively seed self identity and optional contact data.")

    demo = subparsers.add_parser("demo", help="Seed a dedicated fictional demonstration database.")
    demo.add_argument("--reset", action="store_true", help="Replace only the dedicated demo database.")

    return parser
