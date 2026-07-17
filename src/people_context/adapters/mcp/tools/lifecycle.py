"""MCP tools for destructive lifecycle operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.app import ForgetError, MergePeopleError, PersonNotFoundError, RecordNotFoundError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.mcp.server import ToolDeps

_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def register(mcp: FastMCP, deps: ToolDeps) -> None:
    """Register implemented lifecycle tools."""

    @mcp.tool(annotations=_DESTRUCTIVE)
    def merge_people(primary_id: str, duplicate_id: str) -> dict[str, Any]:
        """Merge a duplicate person into a primary person atomically."""
        try:
            return deps.merge_people.execute(primary_id, duplicate_id).model_dump(mode="json")
        except PersonNotFoundError as exc:
            return {"error": "person_not_found", "message": str(exc), "person_id": exc.person_id}
        except MergePeopleError as exc:
            return {"error": exc.code, "message": str(exc)}

    @mcp.tool(annotations=_DESTRUCTIVE)
    def forget(target: str, scope: str) -> dict[str, Any]:
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
