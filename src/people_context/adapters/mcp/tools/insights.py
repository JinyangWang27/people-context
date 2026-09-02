"""Read-only MCP tools for recency and date insights."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.references import resolve_reference
from people_context.app.insights import (
    DEFAULT_CONSOLIDATION_LIMIT,
    DEFAULT_STALE_LIMIT,
    DEFAULT_THRESHOLD_DAYS,
    DEFAULT_TIMELINE_LIMIT,
    DEFAULT_WINDOW_DAYS,
    ConsolidationContextError,
    PersonTimelineError,
    StaleRelationshipsError,
    UpcomingDatesError,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(read_only_hint=True)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register the read-only insight tools."""

    @mcp.tool(annotations=_READ_ONLY)
    async def get_stale_relationships(
        category: str | None = None,
        threshold_days: int = DEFAULT_THRESHOLD_DAYS,
        limit: int = DEFAULT_STALE_LIMIT,
    ) -> dict[str, Any]:
        """Return people you have not interacted with recently, using ordinary interactions only."""
        try:
            return deps.get_stale_relationships.execute(
                category=category,
                threshold_days=threshold_days,
                limit=limit,
            ).model_dump(mode="json")
        except StaleRelationshipsError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    async def get_person_timeline(
        person_id: str | None = None,
        limit: int = DEFAULT_TIMELINE_LIMIT,
        person: str | None = None,
    ) -> dict[str, Any]:
        """Return one person's recent history, newest first, as a bounded chronology.

        Pass `person_id` from `resolve_person`, or `person` (a name or alias) to resolve inline.

        Entries project durable records — interactions, observations, facts, affiliations,
        relationships, and traits — with the stored timestamp each was placed by and which field
        that was. Sensitive and restricted records are never returned by this ordinary tool, and a
        trait names only evidence that is itself ordinary. An unknown or removed person returns
        `found: false` rather than an error.
        """
        target = resolve_reference(deps, person_id=person_id, person=person)
        if isinstance(target, dict):
            return target
        try:
            return deps.get_person_timeline.execute(
                target,
                limit=limit,
                include_sensitive=False,
            ).model_dump(mode="json")
        except PersonTimelineError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    async def get_consolidation_context(
        person_id: str,
        limit: int = DEFAULT_CONSOLIDATION_LIMIT,
    ) -> dict[str, Any]:
        """Return one person's stored facts, traits, and observations plus how they relate.

        Use this before proposing maintenance. `signals` names pairs of records that share a
        predicate or category and says how they stand — `duplicate_fact`, `restated_fact`,
        `contradictory_fact`, `succeeding_fact`, `duplicate_trait`, `divergent_trait` — comparing
        normalized values and inclusive validity periods only. It decides nothing: reading the
        evidence and proposing a `correct_record`, a `supersede_fact`, or a `merge_people` for the
        user to approve is your job, and several observations supporting one trait are separate
        evidence rather than duplicates.

        This read never writes. Sensitive and restricted records are never returned, and a trait
        names only evidence that is itself ordinary. An unknown or removed person returns
        `found: false` rather than an error.
        """
        try:
            return deps.get_consolidation_context.execute(
                person_id,
                limit=limit,
                include_sensitive=False,
            ).model_dump(mode="json")
        except ConsolidationContextError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    async def upcoming_dates(
        window_days: int = DEFAULT_WINDOW_DAYS,
        person_id: str | None = None,
        person: str | None = None,
    ) -> dict[str, Any]:
        """Return ordinary birthdays and dated active reminders inside an inclusive upcoming window.

        Optionally narrow to one person by `person_id` or by `person` (a name or alias).
        """
        if person and not person_id:
            target = resolve_reference(deps, person_id=None, person=person)
            if isinstance(target, dict):
                return target
            person_id = target
        try:
            return deps.list_upcoming_dates.execute(
                window_days=window_days,
                person_id=person_id,
            ).model_dump(mode="json")
        except UpcomingDatesError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}
