"""Read-only MCP tools for recency and date insights."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.app.insights import (
    DEFAULT_STALE_LIMIT,
    DEFAULT_THRESHOLD_DAYS,
    DEFAULT_TIMELINE_LIMIT,
    DEFAULT_WINDOW_DAYS,
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
        person_id: str,
        limit: int = DEFAULT_TIMELINE_LIMIT,
    ) -> dict[str, Any]:
        """Return one person's recent history, newest first, as a bounded chronology.

        Entries project durable records — interactions, observations, facts, affiliations,
        relationships, and traits — with the stored timestamp each was placed by and which field
        that was. Sensitive and restricted records are never returned by this ordinary tool, and a
        trait names only evidence that is itself ordinary. An unknown or removed person returns
        `found: false` rather than an error.
        """
        try:
            return deps.get_person_timeline.execute(
                person_id,
                limit=limit,
                include_sensitive=False,
            ).model_dump(mode="json")
        except PersonTimelineError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    async def upcoming_dates(
        window_days: int = DEFAULT_WINDOW_DAYS,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        """Return ordinary birthdays and dated active reminders inside an inclusive upcoming window."""
        try:
            return deps.list_upcoming_dates.execute(
                window_days=window_days,
                person_id=person_id,
            ).model_dump(mode="json")
        except UpcomingDatesError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}
