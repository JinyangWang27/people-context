"""Command-line interface: read/search commands over the same app-layer use cases as the MCP server.

M1 scope: db-path, list, search, context-backed show, and export. Edit/delete/reindex are documented as
planned (M3) and are intentionally not implemented here yet (see docs/cli.md).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from people_context.adapters.sqlite import SqliteContextReader, SqlitePeopleRepository, open_db
from people_context.app import GetPersonContext, PersonContextResult, ResolvePerson, SearchPeople
from people_context.config import describe_resolution, resolve_db_path
from people_context.domain.person import Person
from people_context.ports.clock import SystemClock

_SUMMARY_WIDTH = 40


@dataclass
class CliContext:
    """Per-invocation composition of the adapters a DB-backed command needs."""

    conn: sqlite3.Connection
    repo: SqlitePeopleRepository
    context_reader: SqliteContextReader
    clock: SystemClock


def _open_context(db: str | None) -> CliContext:
    conn = open_db(resolve_db_path(db))
    return CliContext(
        conn=conn,
        repo=SqlitePeopleRepository(conn),
        context_reader=SqliteContextReader(conn),
        clock=SystemClock(),
    )


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

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, dispatch to the matching subcommand, return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "db-path":
        return _cmd_db_path(args)

    ctx = _open_context(args.db)
    try:
        if args.command == "list":
            return _cmd_list(ctx, args)
        if args.command == "search":
            return _cmd_search(ctx, args)
        if args.command == "show":
            return _cmd_show(ctx, args)
        if args.command == "export":
            return _cmd_export(ctx, args)
        parser.error(f"unknown command: {args.command}")
        return 2
    finally:
        ctx.conn.close()


def _cmd_db_path(args: argparse.Namespace) -> int:
    if args.verbose:
        for line in describe_resolution(args.db):
            print(line)
    else:
        print(resolve_db_path(args.db))
    return 0


def _cmd_list(ctx: CliContext, args: argparse.Namespace) -> int:
    people = ctx.repo.list_people(include_deleted=args.all, limit=args.limit)
    if not people:
        print("No people found.")
        return 0
    _print_table(
        ["ID", "NAME", "ALIASES", "SUMMARY"],
        [_list_row(person) for person in people],
    )
    return 0


def _list_row(person: Person) -> tuple[str, str, str, str]:
    name = person.canonical_name + (" [deleted]" if person.deleted_at else "")
    return (person.id, name, str(len(person.aliases)), _truncate(person.summary or "", _SUMMARY_WIDTH))


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True)))
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def _cmd_search(ctx: CliContext, args: argparse.Namespace) -> int:
    results = SearchPeople(ctx.repo).execute(args.query, limit=args.limit)
    if not results:
        print(f"No matches for '{args.query}'.")
        return 0
    for candidate in results:
        print(f"{candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})  {candidate.match_reason}")
    return 0


def _cmd_show(ctx: CliContext, args: argparse.Namespace) -> int:
    person = ctx.repo.get(args.person)
    if person is not None and person.deleted_at is None:
        context = GetPersonContext(ctx.repo, ctx.context_reader, ctx.clock).execute(person.id, include_sensitive=True)
        _print_context(context)
        return 0

    result = ResolvePerson(ctx.repo, ctx.context_reader, ctx.clock).execute(args.person)
    if not result.candidates:
        print(f"No person found matching '{args.person}'.", file=sys.stderr)
        return 1

    if result.ambiguous:
        print(f"Ambiguous match for '{args.person}'; candidates:", file=sys.stderr)
        for candidate in result.candidates:
            print(
                f"  {candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})",
                file=sys.stderr,
            )
        return 2

    context = GetPersonContext(ctx.repo, ctx.context_reader, ctx.clock).execute(
        result.candidates[0].person_id, include_sensitive=True
    )
    if not context.found:
        print(f"No person found matching '{args.person}'.", file=sys.stderr)
        return 1
    _print_context(context)
    return 0


def _print_context(context: PersonContextResult) -> None:
    identity = context.identity
    if identity is None:
        return
    print(f"{identity.canonical_name} ({identity.id})")
    print(f"  self: {identity.is_self}")
    print(f"  summary: {identity.summary or '(none)'}")
    if identity.aliases:
        print("  aliases:")
        for alias in identity.aliases:
            print(f"    - {alias}")
    else:
        print("  aliases: (none)")
    _print_section(
        "relationships",
        [
            f"{record.relationship.type}: {record.other_person_name} ({record.other_person_id})"
            + (f" — {record.relationship.label}" if record.relationship.label else "")
            for record in context.relationships
        ],
    )
    _print_section(
        "affiliations",
        [f"{record.affiliation.role} at {record.organization_name}" for record in context.affiliations],
    )
    _print_section("facts", [f"{fact.predicate}: {fact.value}" for fact in context.facts])
    _print_section(
        "interactions",
        [
            f"{interaction.occurred_at.date().isoformat()}: {interaction.summary}"
            for interaction in context.interactions
        ],
    )
    _print_section("communication reminders", [reminder.text for reminder in context.reminders])


def _print_section(title: str, items: list[str]) -> None:
    print(f"  {title}:")
    if not items:
        print("    (none)")
        return
    for item in items:
        print(f"    - {item}")


def _cmd_export(ctx: CliContext, args: argparse.Namespace) -> int:
    people = ctx.repo.list_people(include_deleted=True)
    document = {
        "format": "people-context-export",
        "version": 1,
        "exported_at": SystemClock().now().isoformat(),
        "people": [person.model_dump(mode="json") for person in people],
    }
    text = json.dumps(document, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0
