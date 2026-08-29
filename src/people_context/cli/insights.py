"""Read-only insight CLI commands."""

from __future__ import annotations

import argparse
import sys

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app.insights import (
    PersonTimelineError,
    StalePersonResult,
    StaleRelationshipsError,
    TimelineEntry,
    UpcomingDateEntry,
    UpcomingDatesError,
    person_timeline_document,
    render_timeline_json,
)
from people_context.cli.people import resolve_person
from people_context.cli.rendering import print_table, truncate

#: Width one record's display text is rendered at in the human timeline. Stored summaries and
#: observation texts are free-form, so this keeps a long one from unaligning the table; `--json`
#: carries every value in full.
_SUMMARY_DISPLAY_WIDTH = 60

TIMELINE_SENSITIVE_WARNING = (
    "This timeline includes sensitive and restricted records, which the MCP server never "
    "discloses. It is printed to this terminal only; redirecting or sharing it is your own "
    "disclosure decision."
)


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


def cmd_timeline(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Print one person's bounded chronology, newest first."""
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    try:
        result = runtime.use_cases.get_person_timeline.execute(
            person.id,
            limit=args.limit,
            include_sensitive=args.include_sensitive,
        )
    except PersonTimelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not result.found:
        # Only reachable if the person is removed between resolution and the read.
        print(f"No person found matching '{args.person}'.", file=sys.stderr)
        return 1

    # The warning describes the disclosure, not one rendering of it, and goes to stderr so a
    # redirected `--json` document stays byte-identical to the rendered document.
    if args.include_sensitive:
        print(f"Warning: {TIMELINE_SENSITIVE_WARNING}", file=sys.stderr)
    if args.json:
        print(render_timeline_json(person_timeline_document(result)), end="")
        return 0

    print(f"{person.canonical_name} ({person.id})")
    if not result.entries:
        print("No timeline entries.")
        return 0
    print_table(
        ["ID", "WHEN", "BASIS", "TYPE", "SUMMARY", "SENSITIVITY", "SOURCE"],
        [_timeline_row(entry) for entry in result.entries],
    )
    if result.truncated:
        print("\nMore entries exist; raise --limit to see them.")
    return 0


def _timeline_row(entry: TimelineEntry) -> tuple[str, str, str, str, str, str, str]:
    return (
        # The durable record's own id, which is what a later `correct_record` or `forget` takes.
        entry.entry_id,
        entry.effective_at.isoformat(),
        entry.basis,
        entry.entry_type,
        truncate(_timeline_summary(entry), _SUMMARY_DISPLAY_WIDTH),
        # An affiliation or relationship carries no stored level, which is different from
        # carrying one this report would then have to name.
        entry.sensitivity.value if entry.sensitivity is not None else "-",
        entry.source_session_id or "-",
    )


def _timeline_summary(entry: TimelineEntry) -> str:
    """Join the record's own display components without inventing a vocabulary for them."""
    return entry.summary if entry.detail is None else f"{entry.summary}: {entry.detail}"


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
