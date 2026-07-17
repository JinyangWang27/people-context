"""MCP tools for portable export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.mcp.server import ToolDeps

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def register(mcp: FastMCP, deps: ToolDeps) -> None:
    """Register the write-gated maximal-disclosure export tool."""

    @mcp.tool(annotations=_WRITE)
    def export_data() -> dict[str, Any]:
        """Export the complete portable domain dataset."""
        return deps.export_data.execute().model_dump(mode="json")
