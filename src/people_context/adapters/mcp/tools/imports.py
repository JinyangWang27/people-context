"""MCP tools for staged email and mbox import."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.importers.email import ImportExtractionError
from people_context.app.imports import ImportPipelineError

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def _error(exc: ImportPipelineError | ImportExtractionError) -> dict[str, Any]:
    details = exc.details if isinstance(exc, ImportPipelineError) else {}
    return {"error": exc.code, "message": str(exc), **details}


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register header-only extraction, review, and selective commit tools."""

    @mcp.tool(annotations=_WRITE)
    async def import_content(
        source_type: str,
        content: str | None = None,
        path: str | None = None,
        self_sender: str | None = None,
    ) -> dict[str, Any]:
        """Extract and atomically stage header-only candidates from a supported source without bodies.

        Accepted `source_type` values are `email`, `mbox`, `vcard`, `ics`, `linkedin`, `outlook`,
        and `whatsapp`. `self_sender` is an optional chat-export label for the user, such as a
        display name or a bare phone number, used to omit the user's own messages.
        """
        try:
            return deps.import_content.execute(
                source_type,
                content=content,
                path=path,
                self_sender=self_sender,
            ).model_dump(mode="json")
        except (ImportPipelineError, ImportExtractionError) as exc:
            return _error(exc)
        except OSError as exc:
            return {"error": "invalid_path", "message": str(exc), "path": path}

    @mcp.tool(annotations=_WRITE)
    async def stage_candidates(source: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Stage agent-extracted people, interactions, affiliations, facts, observations, traits, and relationships.

        Use this after extracting concise candidates from user-provided notes, meeting transcripts, or other
        agent-visible text. Distinguish what was stated (`fact`), what happened in this source (`observation`),
        and what you inferred (`trait`, which requires an explicit `confidence` and a concise `evidence_note`).
        Relationship candidates carry batch-local `from_ref`/`to_ref` and are ordinary-disclosure only: omit a
        relationship the user would consider sensitive or restricted rather than staging it.

        References are batch-local; raw notes and source text must not be included in candidate fields. A
        request using `observation`, `trait`, or `relationship` is bounded to 500 candidates, a 128-character
        `source`, 1 MiB of candidate JSON, and 8 KiB per string.
        """
        try:
            return deps.stage_candidates.execute(source, candidates).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE)
    async def review_import(batch_id: str) -> dict[str, Any]:
        """Return staged candidates and statuses for one batch."""
        try:
            return deps.review_import.execute(batch_id).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE)
    async def commit_import(batch_id: str, accepted_ids: list[str]) -> dict[str, Any]:
        """Commit accepted people and resolvable interactions idempotently."""
        try:
            return deps.commit_import.execute(batch_id, accepted_ids).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)
