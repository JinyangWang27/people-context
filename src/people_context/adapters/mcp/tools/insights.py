"""Read-only MCP tools for recency and date insights."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.app.insights import (
    DEFAULT_STALE_LIMIT,
    DEFAULT_THRESHOLD_DAYS,
    StaleRelationshipsError,
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
