"""Read-only MCP tools for relationship graph traversal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.app.relationships.graph import GraphTraversalError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(readOnlyHint=True)


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register the two minimal-disclosure graph tools."""

    @mcp.tool(annotations=_READ_ONLY)
    def get_relationship_graph(
        person_id: str,
        depth: int = 2,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return active relationship structure around one person, capped for bounded disclosure."""
        try:
            return deps.get_relationship_graph.execute(person_id, depth=depth, types=types).model_dump(mode="json")
        except GraphTraversalError as exc:
            return {"error": "invalid_depth", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    def find_connection(person_a: str, person_b: str, max_depth: int = 4) -> dict[str, Any]:
        """Return one shortest relationship path, or a structured not-connected result."""
        try:
            return deps.find_connection.execute(person_a, person_b, max_depth=max_depth).model_dump(mode="json")
        except GraphTraversalError as exc:
            return {"error": "invalid_depth", "message": str(exc)}
