"""Process-level capability gates for high-disclosure MCP tools."""

from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def process_elevation_enabled(variable: str) -> bool:
    """Return whether an operator explicitly enabled a process capability.

    These environment variables are read from the MCP server process, not from
    model-supplied tool arguments. They are therefore suitable as an operator
    elevation boundary for tools that must not be enabled by prompt content.
    """
    return os.environ.get(variable, "").strip().lower() in _TRUTHY
