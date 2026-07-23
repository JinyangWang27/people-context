"""MCP tools for communication guidance and philosophy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.tool_errors import call_action
from people_context.app.context import SetCommunicationPhilosophyInput

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register communication guidance and preference tools."""

    @mcp.tool(annotations=_READ_ONLY)
    def get_communication_guidance(person_id: str, situation: str | None = None) -> dict[str, Any]:
        """Return sensitivity-gated signal for client-composed communication advice."""
        return deps.get_communication_guidance.execute(person_id, situation=situation).model_dump(mode="json")

    @mcp.tool(annotations=_WRITE)
    def set_communication_philosophy(text: str) -> dict[str, Any]:
        """Store communication philosophy verbatim while auditing lengths only."""
        return call_action(
            lambda: deps.set_communication_philosophy.execute(SetCommunicationPhilosophyInput(text=text))
        )
