"""Argument parser for the human CLI."""

from __future__ import annotations

import argparse

from people_context.domain.person import AliasKind


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="people-context", description="Inspect and search your people data.")
    parser.add_argument("--db", default=None, help="Explicit database path, overriding other resolution sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_path = subparsers.add_parser("db-path", help="Print the resolved database path.")
    db_path.add_argument("-v", "--verbose", action="store_true", help="Show the full resolution trace.")

    list_cmd = subparsers.add_parser("list", help="List known people.")
    list_cmd.add_argument("--all", action="store_true", help="Include soft-deleted people.")
    list_cmd.add_argument("--limit", type=int, default=None, help="Maximum number of people to list.")

    search = subparsers.add_parser("search", help="Ranked search results for a name query.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10, help="Maximum number of results.")

    show = subparsers.add_parser("show", help="Show a person's full record.")
    show.add_argument("person", help="A person id, or a name to resolve.")

    export = subparsers.add_parser("export", help="JSON dump of all people.")
    export.add_argument("--output", default=None, help="Write to this file instead of stdout.")

    export_vault = subparsers.add_parser("export-vault", help="Export an Obsidian relationship vault.")
    export_vault.add_argument("--output", required=True, help="Empty or marker-owned output directory.")
    export_vault.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include sensitive and restricted facts in files outside server disclosure controls.",
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

    sync_log = subparsers.add_parser("sync-log", help="Inspect the local replayable changelog.")
    sync_log.add_argument("--limit", type=int, default=50, help="Maximum number of recent entries.")
    sync_log.add_argument("--entity", default=None, help="Filter by exact entity id.")
    sync_log.add_argument(
        "--payloads",
        action="store_true",
        help="Include full replay payloads; hidden by default because they may contain sensitive data.",
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
