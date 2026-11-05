"""Tool registration: wire the real and stub tools onto an MCPServer instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from people_context.adapters.mcp.tools import (
    graph,
    groups,
    guidance,
    imports,
    insights,
    lifecycle,
    people,
    portability,
    records,
    reminders,
)
from people_context.adapters.mcp.tools.tool_sets import ToolSet, apply_tool_set

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

__all__ = ["ToolSet", "register_all"]


def register_all(mcp: MCPServer, deps: RuntimeUseCases, tool_set: ToolSet = ToolSet.ALL) -> None:
    """Register the people-context tools that ``tool_set`` advertises (every tool by default)."""
    people.register(mcp, deps)
    guidance.register(mcp, deps)
    reminders.register(mcp, deps)
    records.register(mcp, deps)
    graph.register(mcp, deps)
    groups.register(mcp, deps)
    insights.register(mcp, deps)
    lifecycle.register(mcp, deps)
    portability.register(mcp, deps)
    imports.register(mcp, deps)
    apply_tool_set(mcp, tool_set)
