"""MCP tools for portable export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.security import process_elevation_enabled

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_EXPORT_ENV = "PEOPLE_CONTEXT_MCP_ENABLE_EXPORT"


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register maximal-disclosure export only after operator elevation."""
    if not process_elevation_enabled(_EXPORT_ENV):
        return

    @mcp.tool(annotations=_READ_ONLY)
    def export_data() -> dict[str, Any]:
        """Export the complete portable domain dataset.

        This tool is absent from the normal MCP surface. Prefer the human-operated
        `people-context export` CLI; enable this tool only for a deliberately
        elevated MCP server process.
        """
        return deps.export_data.execute().model_dump(mode="json")
