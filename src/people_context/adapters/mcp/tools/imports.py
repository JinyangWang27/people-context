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
        forced: bool = False,
    ) -> dict[str, Any]:
        """Extract and atomically stage header-only candidates from a supported source without bodies.

        Accepted `source_type` values are `email`, `mbox`, `vcard`, `ics`, `linkedin`, `outlook`,
        and `whatsapp`. `self_sender` is an optional chat-export label for the user, such as a
        display name or a bare phone number, used to omit the user's own messages.

        A `path` import records a receipt for the file it read, so importing that exact file again
        reports the existing batch instead of staging a second copy. `forced` says the repeat is
        intentional: it stages the same content as a distinct processing session and never weakens
        the duplicate rule for later calls. It is also the only way past
        `source_previously_redacted` after a hard forget — and for `mbox`, which is read from a
        path and cannot be resubmitted as inline content, the only way at all.
        """
        try:
            return deps.import_content.execute(
                source_type,
                content=content,
                path=path,
                self_sender=self_sender,
                forced=forced,
            ).model_dump(mode="json")
        except (ImportPipelineError, ImportExtractionError) as exc:
            return _error(exc)
        except OSError as exc:
            return {"error": "invalid_path", "message": str(exc), "path": path}

    @mcp.tool(annotations=_WRITE)
    async def stage_candidates(
        source: str,
        candidates: list[dict[str, Any]],
        source_kind: str | None = None,
        content_digest: str | None = None,
        extraction_fingerprint: str | None = None,
        label: str | None = None,
        external_source_id: str | None = None,
    ) -> dict[str, Any]:
        """Stage agent-extracted people, interactions, affiliations, facts, observations, traits, and relationships.

        Use this after extracting concise candidates from user-provided notes, meeting transcripts, or other
        agent-visible text. Distinguish what was stated (`fact`), what happened in this source (`observation`),
        and what you inferred (`trait`, which requires an explicit `confidence` and a concise `evidence_note`).
        Relationship candidates carry batch-local `from_ref`/`to_ref` and are ordinary-disclosure only: omit a
        relationship the user would consider sensitive or restricted rather than staging it.

        References are batch-local; raw notes and source text must not be included in candidate fields. A
        request using `observation`, `trait`, or `relationship` is bounded to 500 candidates, a 128-character
        `source`, 1 MiB of candidate JSON, and 8 KiB per string.

        A trait may name the records it was drawn from. Give a supporting `observation` or `interaction` any
        short `evidence_ref` label of your own and list those labels in the trait's `evidence_refs`; use
        `evidence_ids` for records already stored. Evidence must be about the trait's own person, and one trait
        cites at most 32 references and ids combined, each at most 256 characters.

        `source_kind` optionally records an import receipt for this batch. It is a machine category such as
        `meeting_transcript`, at most 128 characters of letters, digits, `.`, `_`, `-`, or `/` — never a person,
        a title, or a description; put any human wording in `label` instead. If you can compute a SHA-256 over
        the exact source artifact, pass it as `content_digest` (64 lowercase hex characters) so re-importing that
        same source can be detected; without one, no duplicate detection is promised. `extraction_fingerprint`
        is optional and should be omitted unless you have explicit, bounded configuration semantics for it.
        None of these fields may carry source text.
        """
        try:
            return deps.stage_candidates.execute(
                source,
                candidates,
                source_kind=source_kind,
                content_digest=content_digest,
                extraction_fingerprint=extraction_fingerprint,
                label=label,
                external_source_id=external_source_id,
            ).model_dump(mode="json")
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
