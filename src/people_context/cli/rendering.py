"""Stable text rendering shared by CLI command modules."""

from __future__ import annotations

import shlex
from pathlib import Path

from people_context.app.context import PersonContextResult
from people_context.app.imports import ImportReviewRow, MatchDisposition
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
    """Render review-safe candidate summaries in deterministic staging order.

    Every line leads with the canonical candidate id, because that id is the durable selection
    interface for `pctx import commit` and for onboarding alike — this renderer never mints a
    numbered shorthand a later invocation could not resolve. The status is shown so an already
    committed row is visibly not a pending decision, and a person candidate that the staging
    step matched to somebody already known says so rather than looking like a new identity.
    """
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
            detail = _import_person(candidate)
        elif candidate_type == "affiliation":
            detail = f"{candidate['role']} at {candidate['org']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "fact":
            detail = f"{candidate['predicate']}={candidate['value']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "observation":
            detail = f"{candidate['text']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "trait":
            detail = (
                f"{candidate['category']}={candidate['value']} "
                f"(confidence {candidate['confidence']}) — {_import_owner(candidate, person_names)}"
            )
        elif candidate_type == "relationship":
            detail = _import_relationship(candidate, person_names)
        else:
            detail = _import_interaction(candidate, person_names)
        print(f"  {row.id}  {row.status}  {candidate_type}  {detail}")


#: Enough participants to tell two proposed interactions apart without wrapping the line.
_REVIEWED_PARTICIPANTS = 4


def _import_interaction(candidate: dict[str, object], person_names: dict[str, str]) -> str:
    """Describe one proposed interaction well enough to choose it by id.

    Every interaction in a batch would otherwise render identically, which would leave
    selective `--accept` guessing. The fields shown are the distilled ones the importers
    already staged — a neutral summary, the date, the channel, and who was present — never a
    message body, a subject line, or anything else discarded at extraction.
    """
    parts = [str(candidate.get("summary") or "interaction")]
    date = candidate.get("date")
    if date:
        parts.append(str(date)[:10])
    channel = candidate.get("channel")
    if channel:
        parts.append(str(channel))
    detail = " · ".join(parts)

    raw_participants = candidate.get("participant_candidate_ids")
    participants = [str(value) for value in raw_participants] if isinstance(raw_participants, list) else []
    if not participants:
        return detail
    shown = [
        f"{person_names.get(participant, 'unknown person')} ({participant})"
        for participant in participants[:_REVIEWED_PARTICIPANTS]
    ]
    remaining = len(participants) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining} more")
    return f"{detail} — {', '.join(shown)}"


def _import_person(candidate: dict[str, object]) -> str:
    """Say what staging concluded about one proposed identity, ambiguity included.

    An extraction batch distinguishes "nobody matched" from "several people matched", and only
    the first is a new identity. A reviewer who cannot see that difference cannot make the
    decision the ambiguity is waiting on, so it is said in words rather than left implicit in a
    missing id.
    """
    detail = str(candidate["name"])
    matched_person_id = candidate.get("matched_person_id")
    if candidate.get("match_disposition") == MatchDisposition.AMBIGUOUS.value:
        return f"{detail} — matches {candidate.get('match_count', 'several')} existing people; identity unresolved"
    if matched_person_id:
        detail += f" — matches existing person {matched_person_id}"
    return detail


def _import_relationship(candidate: dict[str, object], person_names: dict[str, str]) -> str:
    """Describe one proposed edge by both endpoints, since neither alone identifies it."""
    from_id = str(candidate["from_candidate_id"])
    to_id = str(candidate["to_candidate_id"])
    subject = f"{person_names.get(from_id, 'unknown person')} ({from_id})"
    obj = f"{person_names.get(to_id, 'unknown person')} ({to_id})"
    return f"{subject} —{candidate['relationship_type']}→ {obj}"


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
