"""MCP tools for staged email and mbox import."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.mcp.tools.tool_errors import flag_refusals
from people_context.app.imports import ImportPipelineError

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(read_only_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def _error(exc: ImportPipelineError | ImportExtractionError) -> dict[str, Any]:
    details = exc.details if isinstance(exc, ImportPipelineError) else {}
    return {"error": exc.code, "message": str(exc), **details}


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register header-only extraction, review, and selective commit tools."""

    @mcp.tool(annotations=_WRITE, structured_output=False)
    @flag_refusals
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

    @mcp.tool(annotations=_WRITE, structured_output=False)
    @flag_refusals
    async def stage_candidates(
        source: str,
        candidates: list[dict[str, Any]],
        source_kind: str | None = None,
        content_digest: str | None = None,
        extraction_fingerprint: str | None = None,
        label: str | None = None,
        external_source_id: str | None = None,
    ) -> dict[str, Any]:
        """Stage agent-extracted people, interactions, records, relationships, groups, and memberships.

        Candidate types are `person`, `interaction`, `affiliation`, `fact`, `observation`, `trait`,
        `relationship`, `group`, and `group_membership`. Stage every candidate from one source in a single
        call. Required fields, beyond `type`; any key the type does not declare is refused:
        `person` `ref`, `name` (optional `aliases`); `affiliation` `person_ref`, `org`, `role`; `fact` `person_ref`,
        `predicate`, `value`; `observation` `person_ref`, `text`; `trait` `person_ref`, `category`, `value`,
        `evidence_note`, `confidence`; `interaction` `summary`, `participant_refs`, `date`; `relationship`
        `from_ref`, `to_ref`, `relationship_type`; `group` `ref`, `name`, `kind`; `group_membership` `person_ref`,
        `group_ref`. Only `person` and `group` carry a `ref`; the rest point at one, and refs are batch-local.
        Staging matches each `person` against stored people, so do not resolve names first. An email address is
        a `{"value": <address>, "kind": "handle"}` alias, never a `name`.

        A `fact` is stated, an `observation` happened in this source, and a `trait` is inferred. Never put raw
        source text in a field, and leave out a relationship the user would consider sensitive. Staging writes
        nothing durable: the user reviews with `review_import` and accepts with `commit_import`. Before staging
        `stated_by`, trait evidence, groups and memberships, dates, or the receipt fields `source_kind`, `label`,
        `content_digest`, `extraction_fingerprint`, and `external_source_id`, read the "Staged capture" and
        "Documents and shared contexts" sections of the `people-context://guide` resource.
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

    @mcp.tool(annotations=_READ_ONLY, structured_output=False)
    @flag_refusals
    async def review_import(batch_id: str) -> dict[str, Any]:
        """Return staged candidates and statuses for one batch.

        The response carries a `batch_digest`. Pass it back to `amend_candidate`,
        `withdraw_candidates`, or `commit_import` to have the action refused if somebody else
        changed the batch after this read.

        A new-person row may carry `possible_matches`: existing people sharing its given name or a
        short form of it. They are not matches. Ask the user; to reuse one, pass its id as
        `matched_person_id` to `amend_candidate`, otherwise commit to create a new person.
        A row with `name_is_email: true` stays unresolved at commit: ask the user for the person's name and
        amend `name` to it, keeping the address as a `handle` alias.
        """
        try:
            return deps.review_import.execute(batch_id, suggest_possible_matches=True).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE, structured_output=False)
    @flag_refusals
    async def amend_candidate(
        batch_id: str,
        candidate_id: str,
        patch: dict[str, Any],
        expected_batch_digest: str | None = None,
    ) -> dict[str, Any]:
        """Correct one staged candidate before it is committed, returning the revised batch.

        The patch replaces the named fields and leaves the rest alone. Nothing is committed:
        confirming an amendment is not acceptance of the batch.
        """
        try:
            return deps.amend_staged_candidate.execute(
                batch_id,
                candidate_id,
                patch,
                expected_batch_digest=expected_batch_digest,
            ).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE, structured_output=False)
    @flag_refusals
    async def withdraw_candidates(
        batch_id: str,
        candidate_ids: list[str],
        expected_batch_digest: str | None = None,
    ) -> dict[str, Any]:
        """Drop staged candidates from consideration, returning the revised batch.

        A withdrawn candidate stays listed as `rejected` so the user can see what was dropped, and
        is never committed. Nothing else about the batch changes.
        """
        try:
            return deps.withdraw_staged_candidates.execute(
                batch_id,
                candidate_ids,
                expected_batch_digest=expected_batch_digest,
            ).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)

    @mcp.tool(annotations=_WRITE, structured_output=False)
    @flag_refusals
    async def commit_import(
        batch_id: str,
        accepted_ids: list[str],
        expected_batch_digest: str | None = None,
    ) -> dict[str, Any]:
        """Commit accepted people and resolvable interactions idempotently.

        The receipt is the verification: an id in `committed_ids` is stored. Read records back only for ids
        in `unresolved_ids` or `skipped_ids`, or when the user asks to see them. Accepting a withdrawn
        candidate refuses the whole commit. Supplying the
        `expected_batch_digest` from the review the user approved refuses it if the batch moved
        after they saw it.
        """
        try:
            return deps.commit_import.execute(
                batch_id,
                accepted_ids,
                expected_batch_digest=expected_batch_digest,
            ).model_dump(mode="json")
        except ImportPipelineError as exc:
            return _error(exc)
