"""MCP prompts and resources: the usage guidance, reachable from any client.

The packaged Claude Code skill teaches agents the patterns this server expects — resolve before
reading, context versus guidance, propose-then-commit capture. Clients that do not load skills
(Claude Desktop, Cursor, Codex, anything generic) saw only the short instructions string. The
same guidance is exposed here through the protocol itself: one resource carrying the full guide,
one carrying the user's own record, and a handful of prompts that compose the tools into the
workflows the skill describes. Nothing here reads or writes personal data beyond what the
ordinary tools already disclose, and no prompt commits anything.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.ports.repository import PersonReader

GUIDE_URI = "people-context://guide"
SELF_URI = "people-context://self"

_GUIDE_RESOURCE = "guide.md"


def load_guide() -> str:
    """Return the packaged usage guide (the body of the Claude Code usage skill)."""
    return resources.files("people_context.adapters.mcp").joinpath(_GUIDE_RESOURCE).read_text(encoding="utf-8")


def register_prompts(mcp: MCPServer, people: PersonReader) -> None:
    """Register the guide and self resources plus the workflow prompts."""

    @mcp.resource(
        GUIDE_URI,
        name="people-context-guide",
        title="How to use people-context",
        description="Resolution-first reads, context vs. guidance, meeting prep, and propose-then-commit capture.",
        mime_type="text/markdown",
    )
    async def guide() -> str:
        return load_guide()

    @mcp.resource(
        SELF_URI,
        name="people-context-self",
        title="The user's own record",
        description="Narrow identity of the person marked as self: id, canonical name, aliases, summary.",
        mime_type="application/json",
    )
    async def self_record() -> str:
        # Async on purpose: the SDK runs sync handlers on a worker thread, and the SQLite
        # connection behind `people` is bound to the loop thread the tools run on.
        person = people.get_self()
        if person is None:
            payload: dict[str, Any] = {
                "found": False,
                "hint": "Run `pctx init` or `remember_person` with is_self=true.",
            }
        else:
            payload = {
                "found": True,
                "person_id": person.id,
                "canonical_name": person.canonical_name,
                "aliases": [alias.value for alias in person.aliases],
                "summary": person.summary,
            }
        return json.dumps(payload, indent=2)

    @mcp.prompt(name="who", title="Who is this person?", description="Identify one person and read what is stored.")
    def who(name: str) -> str:
        return (
            f"The user asked who {name!r} is.\n\n"
            f"1. Call `resolve_person` with query {name!r}; put any organisation, role, or relationship "
            "detail in `hints` rather than in the query.\n"
            "2. If the result is `ambiguous`, list the candidates with their match reason and ask which "
            "one is meant. Do not read context for a guessed identity.\n"
            "3. If there is a confident top candidate, call `get_person_context` with its `person_id` and "
            "answer from that bundle: who they are, how they relate to the user, affiliations, and what "
            "happened recently. `withheld` counts records the ordinary view did not disclose; mention that "
            "something is withheld rather than reporting an empty record.\n"
            "4. If nothing matches, say so and offer to remember them.\n"
        )

    @mcp.prompt(
        name="remember",
        title="Remember something about a person",
        description="Record one direct statement with the `remember` tool, or stage extracted material for review.",
    )
    def remember(statement: str) -> str:
        return (
            f"The user wants to remember: {statement!r}\n\n"
            "If this is a direct statement about one person — a fact, a preference, an affiliation, how the "
            "user relates to them, or something that happened — call `remember` once with `person` set to "
            "the name as the user said it and `note`, `org`, `role`, or `relationship` as appropriate. "
            "Set `sensitivity` to `sensitive` or `restricted` for health, financial, or otherwise private "
            "matters. A `status` of `ambiguous` or `unconfirmed` means nothing was recorded: show the "
            "candidates, ask, and call again with the exact name.\n\n"
            "If the material was extracted or inferred from earlier conversation, a transcript, or notes, "
            "do not write it directly: call `stage_candidates` with concise structured candidates and tell "
            "the user the batch is pending review. Never call `commit_import` yourself.\n"
        )

    @mcp.prompt(
        name="meeting_prep",
        title="Prepare for a meeting",
        description="One short brief per attendee from stored context, guidance, and open follow-ups.",
    )
    def meeting_prep(attendees: str) -> str:
        return (
            f"The user is about to meet: {attendees}\n\n"
            "For each attendee, one at a time:\n"
            "1. `resolve_person` — on `ambiguous`, ask before reading anything.\n"
            "2. `get_person_context` with `include_communication: true` for who they are and what happened last.\n"
            "3. `get_communication_guidance` for how to communicate; do not infer tone from context alone.\n"
            "4. `list_reminders` with their `person_id` for open follow-ups.\n\n"
            "Then write one short brief per person: who they are, how they relate to the user, what happened "
            "last, open follow-ups, and how to communicate with them. This is read-only: record nothing the "
            "meeting has not produced yet.\n"
        )

    @mcp.prompt(
        name="end_of_session_capture",
        title="Propose what to remember from this session",
        description="Review what was learned about people and stage it for the user to review; never commit.",
    )
    def end_of_session_capture() -> str:
        return (
            "The session is wrapping up. Briefly review what you genuinely learned about people during it — "
            "a durable fact, a role change, a meaningful interaction. If there is anything, propose it with "
            "`stage_candidates` as concise structured candidates (never raw conversation text) and tell the "
            "user the batch is pending review. Never call `commit_import`. If nothing durable was learned, "
            "say nothing.\n"
        )

    @mcp.prompt(
        name="maintenance_review",
        title="Review what is stored about someone",
        description="Read timeline and consolidation signals, then propose corrections and wait for approval.",
    )
    def maintenance_review(name: str) -> str:
        return (
            f"The user wants to review what is stored about {name!r}.\n\n"
            "1. `resolve_person`; an `ambiguous` result stops the review.\n"
            "2. Read `get_person_context`, `get_person_timeline`, and `get_consolidation_context`.\n"
            "3. Read the `signals` (duplicate, restated, contradictory, succeeding facts; duplicate or "
            "divergent traits). They are evidence for your judgement, not a verdict.\n"
            "4. Propose structured actions by id: `correct_record` for a value that was wrong, "
            "`supersede_fact` for a value that was right and then changed, `merge_people` only when identity "
            "is independently established.\n"
            "5. Wait for explicit approval per proposal before calling any mutation tool; then re-read and "
            "report the resulting state.\n"
        )
