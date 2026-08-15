"""Process-level capability gates for high-disclosure MCP tools.

The gate itself lives in `people_context.config` alongside the rest of this project's
environment resolution, because the CLI now reports the same gate state and both readers must
agree on it exactly. This module keeps the MCP layer's own name for it.
"""

from __future__ import annotations

from people_context.config import EXPORT_ENV, SENSITIVE_CONTEXT_ENV, process_elevation_enabled

__all__ = ["EXPORT_ENV", "SENSITIVE_CONTEXT_ENV", "process_elevation_enabled"]
