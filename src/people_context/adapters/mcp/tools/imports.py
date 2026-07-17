"""MCP tools for staged email and mbox import."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.email_import import ImportExtractionError
from people_context.app import ImportPipelineError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.mcp.server import ToolDeps

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def _error(exc: ImportPipelineError | ImportExtractionError) -> dict[str, Any]:
    details = exc.details if isinstance(exc, ImportPipelineError) else {}
    return {"error": exc.code, "message": str(exc), **details}


def register(mcp: FastMCP, deps: ToolDeps) -> None:
    """Register header-only extraction, review, and selective commit tools."""

    @mcp.tool(annotations=_WRITE)
    def import_content(source_type: str, content: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Extract and atomically stage email header candidates without bodies."""
        try:
            return deps.import_content.execute(source_type, content=content, path=path).model_dump(mode="json")
        except (ImportPipelineError, ImportExtractionError) as exc:
            return _error(exc)
        except OSError as exc:
            return {"error": "invalid_path", "message": str(exc), "path": path}

    @mcp.tool(annotations=_WRITE)
    def stage_candidates(source: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and atomically stage agent-extracted people, interactions, affiliations, and facts.

        Use this after extracting concise candidates from user-provided notes or other agent-visible text.
        References are batch-local; raw notes and source text must not be included in candidate fields.
        """
        try:
            return deps.stage_candidates.execute(source, candidates).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE)
    def review_import(batch_id: str) -> dict[str, Any]:
        """Return staged candidates and statuses for one batch."""
        try:
            return deps.review_import.execute(batch_id).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE)
    def commit_import(batch_id: str, accepted_ids: list[str]) -> dict[str, Any]:
        """Commit accepted people and resolvable interactions idempotently."""
        try:
            return deps.commit_import.execute(batch_id, accepted_ids).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)
