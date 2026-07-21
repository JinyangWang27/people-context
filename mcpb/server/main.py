"""MCPB native-UV entry point for the people-context stdio MCP server.

The host application's UV runtime installs the pinned ``people-context`` release
declared in the sibling ``pyproject.toml`` and then executes this file. It adds no
behavior of its own: it delegates to the packaged server entry point so the bundle
runs exactly the same stdio server as ``people-context-mcp``.
"""

from __future__ import annotations

from people_context.adapters.mcp.server import main

if __name__ == "__main__":
    main()
