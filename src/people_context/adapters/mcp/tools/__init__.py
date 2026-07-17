"""Tool registration: wire the real and stub tools onto a FastMCP instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from people_context.adapters.mcp.tools import imports, lifecycle, m2, people, portability

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.mcp.server import ToolDeps

__all__ = ["register_all"]


def register_all(mcp: FastMCP, deps: ToolDeps) -> None:
    """Register every people-context tool (real use cases + typed stubs)."""
    people.register(mcp, deps)
    m2.register(mcp, deps)
    lifecycle.register(mcp, deps)
    portability.register(mcp, deps)
    imports.register(mcp, deps)
