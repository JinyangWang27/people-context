"""Stable text rendering shared by CLI command modules."""

from __future__ import annotations

import shlex
from collections import Counter
from pathlib import Path

from people_context.app.context import PersonContextResult
from people_context.app.imports import ImportReviewRow, MatchDisposition
from people_context.domain.import_provenance import STAGING_STATUS_REJECTED
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
    """Render a batch summary, then one numbered line per candidate in staging order.

    Every line leads with the row's `#n` ordinal, which `pctx import commit --accept` and onboarding
    select by, and ends with the canonical id, which stays the durable interface and stays
    copyable. The status is shown so an already committed or withdrawn row is visibly not a
    pending decision.
    """
    print(import_review_summary(rows))
    print("Import candidates:")
    for line in import_review_lines(rows):
        print(f"  {line}")


def import_review_summary(rows: list[ImportReviewRow]) -> str:
    """Say what a batch holds before a reviewer reads it line by line.

    Withdrawn rows are counted once, as withdrawn, and nowhere else: they are no longer a proposal
    about anybody.
    """
    live = [row for row in rows if row.status != STAGING_STATUS_REJECTED]
    dispositions = Counter(
        str(row.candidate.get("match_disposition") or _person_disposition(row.candidate))
        for row in live
        if row.candidate["type"] == "person"
    )
    types = Counter(str(row.candidate["type"]) for row in live)
    people = sum(dispositions.values())
    parts = [
        f"{people} people ({dispositions[MatchDisposition.UNMATCHED.value]} new, "
        f"{dispositions[MatchDisposition.MATCHED.value]} matched, "
        f"{dispositions[MatchDisposition.AMBIGUOUS.value]} ambiguous)"
    ]
    parts.extend(f"{count} {candidate_type}" for candidate_type, count in sorted(types.items()))
    withdrawn = len(rows) - len(live)
    return f"Summary: {'; '.join(parts)}; {withdrawn} withdrawn."


def _person_disposition(candidate: dict[str, object]) -> str:
    """A person row staged before dispositions were recorded is matched exactly when it names someone."""
    matched = MatchDisposition.MATCHED if candidate.get("matched_person_id") else MatchDisposition.UNMATCHED
    return matched.value


def import_review_lines(rows: list[ImportReviewRow]) -> list[str]:
    """Return one `#n  status  type  detail  id` line per row, resolving names across the whole batch."""
    person_names = {
        row.id: str(row.candidate["name"])
        for row in rows
        if row.candidate["type"] == "person"
    }
    group_names = {
        row.id: str(row.candidate["name"])
        for row in rows
        if row.candidate["type"] == "group"
    }
    lines = []
    for row in rows:
        candidate = row.candidate
        candidate_type = candidate["type"]
        if candidate_type == "person":
            detail = _import_person(candidate)
        elif candidate_type == "affiliation":
            detail = (
                f"{candidate['role']} at {candidate['org']} — "
                f"{_import_owner(candidate, person_names)}{_import_attribution(candidate)}"
            )
        elif candidate_type == "fact":
            detail = (
                f"{candidate['predicate']}={candidate['value']} — "
                f"{_import_owner(candidate, person_names)}{_import_attribution(candidate)}"
            )
        elif candidate_type == "observation":
            detail = f"{candidate['text']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "trait":
            detail = (
                f"{candidate['category']}={candidate['value']} "
                f"(confidence {candidate['confidence']}) — {_import_owner(candidate, person_names)}"
            )
        elif candidate_type == "relationship":
            detail = _import_relationship(candidate, person_names)
        elif candidate_type == "group":
            detail = _import_group(candidate)
        elif candidate_type == "group_membership":
            detail = _import_membership(candidate, person_names, group_names)
        else:
            detail = _import_interaction(candidate, person_names)
        lines.append(f"#{row.ordinal}  {row.status}  {candidate_type}  {detail}  {row.id}")
    return lines


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


def _import_group(candidate: dict[str, object]) -> str:
    """Describe one proposed group, saying plainly whether it would create or reuse one.

    The difference is the whole decision a reviewer is making here. Committing a candidate that
    names no `group_id` adds another group under a name that may already exist, and M28 offers
    no merge to undo it, so "new group" is said in words rather than left to a missing field.
    """
    detail = f"{candidate['name']} ({candidate['kind']})"
    group_id = candidate.get("group_id")
    detail += f" — records into existing group {group_id}" if group_id else " — new group"
    return detail + _import_attribution(candidate)


def _import_membership(
    candidate: dict[str, object],
    person_names: dict[str, str],
    group_names: dict[str, str],
) -> str:
    """Describe one proposed membership, stating what its dates do and do not establish.

    The basis is shown rather than the dates alone, because absent bounds are the one thing a
    reviewer must not read as "still going": an `unknown` membership supports shared context and
    never a classmate or teammate label, and that difference has to be visible before commit.
    """
    group_candidate_id = str(candidate["group_candidate_id"])
    group = f"{group_names.get(group_candidate_id, 'unknown group')} ({group_candidate_id})"
    basis = str(candidate["temporal_basis"])
    if basis == "unknown":
        period = "dates unknown"
    else:
        period = f"{candidate.get('valid_from') or '?'}–{candidate.get('valid_to') or '?'}"
    return (
        f"{_import_owner(candidate, person_names)} as {candidate.get('role', 'member')} in {group} "
        f"— {basis}: {period}{_import_attribution(candidate)}"
    )


def _import_owner(candidate: dict[str, object], person_names: dict[str, str]) -> str:
    person_candidate_id = str(candidate["person_candidate_id"])
    person_name = str(person_names.get(person_candidate_id, "unknown person"))
    return f"{person_name} ({person_candidate_id})"


def _import_attribution(candidate: dict[str, object]) -> str:
    """Render who asserted a fact or affiliation, or nothing when the candidate names nobody.

    The review gate is where someone decides what to commit, so it has to show the attribution
    that will be committed with the record. Without it a claim a source made about itself reads
    on this line exactly like one the operator established independently.
    """
    stated_by = candidate.get("stated_by")
    return f", stated by {stated_by}" if stated_by else ""


#: Printed once at the end of onboarding and the demo. The project is found through GitHub search, so
#: an honest, single-line request is the cheapest thing that helps the next person find it.
CLIENT_SETUP_HINT = (
    "Connect a client any time with `pctx setup <client>` "
    "(claude-desktop, claude-code, codex, cursor, windsurf, vscode)."
)
STAR_HINT = "If this is useful, a star helps others find it: https://github.com/JinyangWang27/people-context"


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
    print()
    print(STAR_HINT)
