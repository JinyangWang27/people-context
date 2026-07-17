"""MCP stdio adapter: FastMCP server exposing the people-context tool surface."""

from __future__ import annotations

from people_context.adapters.mcp.server import build_server, main

__all__ = ["build_server", "main"]
