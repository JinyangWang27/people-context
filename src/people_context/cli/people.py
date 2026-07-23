"""People, identity, preference, and lifecycle CLI commands."""

from __future__ import annotations

import argparse
import sys

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.context import SetCommunicationPhilosophyInput
from people_context.app.people import AddAliasInput, EditPersonInput, PersonNameCollisionError
from people_context.cli.rendering import print_context, print_table, truncate
from people_context.domain.person import AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY

_SUMMARY_WIDTH = 40


def cmd_list(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """List known people."""
    people = runtime.repo.list_people(include_deleted=args.all, limit=args.limit)
    if not people:
        print("No people found.")
        return 0
    print_table(
        ["ID", "NAME", "ALIASES", "SUMMARY"],
        [_list_row(person) for person in people],
    )
    return 0


def _list_row(person: Person) -> tuple[str, str, str, str]:
    name = person.canonical_name + (" [deleted]" if person.deleted_at else "")
    return (person.id, name, str(len(person.aliases)), truncate(person.summary or "", _SUMMARY_WIDTH))


def cmd_search(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Search known people by ranked name match."""
    results = runtime.use_cases.search_people.execute(args.query, limit=args.limit)
    if not results:
        print(f"No matches for '{args.query}'.")
        return 0
    for candidate in results:
        print(f"{candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})  {candidate.match_reason}")
    return 0


def cmd_show(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Show a sensitivity-unrestricted local CLI context."""
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    context = runtime.use_cases.get_person_context.execute(person.id, include_sensitive=True)
    print_context(context)
    return 0


def resolve_person(runtime: ApplicationRuntime, reference: str) -> tuple[Person | None, int]:
    """Resolve a CLI id-or-name reference with stable diagnostics."""
    person = runtime.repo.get(reference)
    if person is not None and person.deleted_at is None:
        return person, 0
    result = runtime.use_cases.resolve_person.execute(reference)
    if not result.candidates:
        print(f"No person found matching '{reference}'.", file=sys.stderr)
        return None, 1
    if result.ambiguous:
        print(f"Ambiguous match for '{reference}'; candidates:", file=sys.stderr)
        for candidate in result.candidates:
            print(f"  {candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})", file=sys.stderr)
        return None, 2
    resolved = runtime.repo.get(result.candidates[0].person_id)
    if resolved is None or resolved.deleted_at is not None:
        print(f"No person found matching '{reference}'.", file=sys.stderr)
        return None, 1
    return resolved, 0


def cmd_edit(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Edit a person's canonical name or summary."""
    if args.name is None and args.summary is None:
        print("edit requires at least one of --name or --summary.", file=sys.stderr)
        return 2
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    try:
        updated = runtime.use_cases.edit_person.execute(
            EditPersonInput(person_id=person.id, name=args.name, summary=args.summary)
        )
    except PersonNameCollisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Updated {updated.canonical_name} ({updated.id}).")
    return 0


def cmd_add_alias(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Add an alias to an active person."""
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    updated = runtime.use_cases.add_alias.execute(
        AddAliasInput(
            person_id=person.id,
            value=args.value,
            kind=AliasKind(args.kind),
            lang=args.lang,
            script=args.script,
            source="cli",
        )
    )
    print(f"Alias recorded for {updated.canonical_name} ({updated.id}).")
    return 0


def cmd_set(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Set a supported user preference."""
    if args.key != PREF_COMMUNICATION_PHILOSOPHY:
        print(f"Unsupported preference key: {args.key}", file=sys.stderr)
        return 2
    runtime.use_cases.set_communication_philosophy.execute(
        SetCommunicationPhilosophyInput(text=args.value, source="cli")
    )
    print(f"Set {args.key}.")
    return 0


def cmd_delete(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Preview and permanently forget an active person."""
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    preview = runtime.use_cases.preview_forget.execute(person.id)
    print(f"Delete {preview.canonical_name} ({preview.person_id}) permanently?")
    for entity_type, count in preview.deleted.items():
        if count:
            print(f"  {entity_type}: {count}")
    if not args.yes and input("Proceed? [y/N] ").strip().casefold() not in {"y", "yes"}:
        print("Aborted.")
        return 0
    runtime.use_cases.forget.execute(person.id, "person", source="cli")
    print("Deleted.")
    return 0
