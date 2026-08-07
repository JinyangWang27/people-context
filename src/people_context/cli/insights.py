"""Read-only insight CLI commands."""

from __future__ import annotations

import argparse
import sys

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.insights import (
    StalePersonResult,
    StaleRelationshipsError,
    UpcomingDateEntry,
    UpcomingDatesError,
)
from people_context.cli.people import resolve_person
from people_context.cli.rendering import print_table


def cmd_stale(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Report people whose ordinary interaction recency reached the threshold."""
    try:
        result = runtime.use_cases.get_stale_relationships.execute(
            category=args.category,
            threshold_days=args.threshold_days,
            limit=args.limit,
        )
    except StaleRelationshipsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not result.people:
        print("No stale relationships.")
        return 0
    print_table(
        ["ID", "NAME", "CATEGORIES", "LAST INTERACTION", "DAYS", "COUNT"],
        [_stale_row(row) for row in result.people],
    )
    if result.truncated:
        print("\nMore people qualify; raise --limit to see them.")
    return 0


def cmd_upcoming(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Report birthdays and dated reminders falling inside the upcoming window."""
    person_id: str | None = None
    if args.person is not None:
        person, exit_code = resolve_person(runtime, args.person)
        if person is None:
            return exit_code
        person_id = person.id
    try:
        result = runtime.use_cases.list_upcoming_dates.execute(
            window_days=args.window_days,
            person_id=person_id,
        )
    except UpcomingDatesError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not result.entries:
        print("No upcoming dates.")
    else:
        print_table(
            ["DATE", "KIND", "NAME", "LABEL"],
            [_upcoming_row(entry) for entry in result.entries],
        )
    if result.skipped_unparseable:
        print(f"\nSkipped {result.skipped_unparseable} birthday fact(s) with an unrecognized date value.")
    return 0


def _upcoming_row(entry: UpcomingDateEntry) -> tuple[str, str, str, str]:
    return (entry.date.isoformat(), entry.kind.value, entry.name, entry.label)


def _stale_row(row: StalePersonResult) -> tuple[str, str, str, str, str, str]:
    last_interaction = "never" if row.last_interaction_at is None else row.last_interaction_at.date().isoformat()
    return (
        row.person_id,
        row.name,
        ", ".join(row.categories) or "-",
        last_interaction,
        "-" if row.days_since is None else str(row.days_since),
        str(row.interaction_count),
    )
