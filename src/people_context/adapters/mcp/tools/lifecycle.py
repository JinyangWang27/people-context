"""MCP tools for destructive lifecycle operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.tool_errors import flag_refusals
from people_context.app.people import ForgetError, MergePeopleError
from people_context.app.records import PersonNotFoundError, RecordNotFoundError

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register implemented lifecycle tools."""

    @mcp.tool(annotations=_DESTRUCTIVE)
    @flag_refusals
    async def merge_people(primary_id: str, duplicate_id: str) -> dict[str, Any]:
        """Merge a duplicate person into a primary person atomically."""
        try:
            return deps.merge_people.execute(primary_id, duplicate_id).model_dump(mode="json")
        except PersonNotFoundError as exc:
            return {"error": "person_not_found", "message": str(exc), "person_id": exc.person_id}
        except MergePeopleError as exc:
            return {"error": exc.code, "message": str(exc)}

    @mcp.tool(annotations=_DESTRUCTIVE)
    @flag_refusals
    async def forget(target: str, scope: str) -> dict[str, Any]:
        """Hard-delete a person or record and redact identifying audit history."""
        try:
            return deps.forget.execute(target, scope).model_dump(mode="json")
        except PersonNotFoundError as exc:
            return {"error": "person_not_found", "message": str(exc), "person_id": exc.person_id}
        except RecordNotFoundError as exc:
            return {
                "error": "record_not_found",
                "message": str(exc),
                "entity_type": exc.entity_type,
                "entity_id": exc.entity_id,
            }
        except ForgetError as exc:
            return {"error": exc.code, "message": str(exc), **exc.details}
