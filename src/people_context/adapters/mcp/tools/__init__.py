"""Tool registration: wire the real and stub tools onto an MCPServer instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from people_context.adapters.mcp.tools import (
    graph,
    guidance,
    imports,
    insights,
    lifecycle,
    people,
    portability,
    records,
    reminders,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

__all__ = ["register_all"]


def register_all(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register every people-context tool (real use cases + typed stubs)."""
    people.register(mcp, deps)
    guidance.register(mcp, deps)
    reminders.register(mcp, deps)
    records.register(mcp, deps)
    graph.register(mcp, deps)
    insights.register(mcp, deps)
    lifecycle.register(mcp, deps)
    portability.register(mcp, deps)
    imports.register(mcp, deps)
