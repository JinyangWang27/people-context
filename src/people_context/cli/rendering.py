"""Stable text rendering shared by CLI command modules."""

from __future__ import annotations

import shlex
from pathlib import Path

from people_context.app.context import PersonContextResult
from people_context.app.imports import ImportReviewRow
from people_context.domain.person import Person


def print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    """Print a whitespace-aligned table."""
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True)))
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def truncate(text: str, width: int) -> str:
    """Truncate text to a stable display width."""
    return text if len(text) <= width else text[: width - 1] + "…"


def print_context(context: PersonContextResult) -> None:
    """Render a full CLI person context."""
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
            f"{record.display_type}: {record.other_person_name} ({record.other_person_id})"
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


def print_import_review(rows: list[ImportReviewRow]) -> None:
    """Render review-safe candidate summaries."""
    print("Import candidates:")
    person_names = {
        row.id: str(row.candidate["name"])
        for row in rows
        if row.candidate["type"] == "person"
    }
    for row in rows:
        candidate = row.candidate
        candidate_type = candidate["type"]
        if candidate_type == "person":
            detail = candidate["name"]
        elif candidate_type == "affiliation":
            detail = f"{candidate['role']} at {candidate['org']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "fact":
            detail = f"{candidate['predicate']}={candidate['value']} — {_import_owner(candidate, person_names)}"
        else:
            detail = "summary-only interaction"
        print(f"  {row.id}  {candidate_type}  {detail}")


def _import_owner(candidate: dict[str, object], person_names: dict[str, str]) -> str:
    person_candidate_id = str(candidate["person_candidate_id"])
    person_name = str(person_names.get(person_candidate_id, "unknown person"))
    return f"{person_name} ({person_candidate_id})"


def print_demo_instructions(demo_path: Path, people: dict[str, Person]) -> None:
    """Print stable next steps for the fictional demo."""
    print(f"Demo database: {demo_path}")
    print(f"Start MCP server: people-context-mcp --db {shlex.quote(str(demo_path))}")
    print(f'resolve_person {{"query": "{people["amina"].canonical_name}"}}')
    print(f'get_relationship_graph {{"person_id": "{people["amina"].id}", "depth": 2}}')
    print(
        f'find_connection {{"person_a": "{people["self"].id}", '
        f'"person_b": "{people["sofia"].id}"}}'
    )
