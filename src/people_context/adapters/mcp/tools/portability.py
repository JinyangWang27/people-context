"""MCP tools for portable export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.security import EXPORT_ENV, process_elevation_enabled
from people_context.adapters.mcp.tools.tool_errors import flag_refusals

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(read_only_hint=True)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register maximal-disclosure export only after operator elevation."""
    if not process_elevation_enabled(EXPORT_ENV):
        return

    @mcp.tool(annotations=_READ_ONLY)
    @flag_refusals
    async def export_data() -> dict[str, Any]:
        """Export the complete portable domain dataset.

        This tool is absent from the normal MCP surface. Prefer the human-operated
        `pctx export` CLI; enable this tool only for a deliberately
        elevated MCP server process.
        """
        return deps.export_data.execute().model_dump(mode="json")
