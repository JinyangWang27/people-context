"""Real tools: identity resolution, people search, and remembering a person.

These call the app use cases directly and return JSON-serialisable dicts
(`model_dump(mode="json")`). Domain-level conflicts (ambiguous names, an
existing self) are mapped to structured error dicts rather than raised, so the
client model gets an actionable payload instead of a protocol error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.security import process_elevation_enabled
from people_context.app.people import (
    AliasInput,
    AmbiguousPersonError,
    RememberPersonInput,
    ResolutionHints,
    SelfAlreadyExistsError,
)
from people_context.app.semantic import SemanticSearchValidationError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_SENSITIVE_CONTEXT_ENV = "PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE"


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register the resolve/search/remember tools bound to the given use cases."""

    @mcp.tool(annotations=_READ_ONLY)
    def resolve_person(query: str, hints: dict[str, Any] | None = None, limit: int = 5) -> dict[str, Any]:
        """Resolve a name, nickname, or partial reference to candidate people.

        Call this first whenever the user mentions someone, before asking who they
        mean. Returns ranked candidates with a score and match reason. If two or
        more candidates are close, the result is flagged `ambiguous` and all are
        returned so you can disambiguate with extra context or a clarifying
        question. An empty candidate list means no confident match — use
        `remember_person` to create a new record.
        """
        validated_hints = ResolutionHints.model_validate(hints) if hints is not None else None
        return deps.resolve_person.execute(query, limit=limit, hints=validated_hints).model_dump(mode="json")

    @mcp.tool(annotations=_READ_ONLY)
    def get_person_context(
        person_id: str,
        purpose: str | None = None,
        max_items: int = 10,
    ) -> dict[str, Any]:
        """Assemble a minimal-disclosure context bundle for one person.

        Returns narrow identity fields, active relationships and affiliations, and
        one ranked facts/interactions slice capped by `max_items`. Sensitive and
        restricted records are never returned by this ordinary tool. Communication
        traits require a purpose containing `communication`.
        """
        return deps.get_person_context.execute(
            person_id,
            purpose=purpose,
            max_items=max_items,
            include_sensitive=False,
        ).model_dump(mode="json")

    if process_elevation_enabled(_SENSITIVE_CONTEXT_ENV):

        @mcp.tool(annotations=_READ_ONLY)
        def get_sensitive_person_context(
            person_id: str,
            purpose: str | None = None,
            max_items: int = 10,
        ) -> dict[str, Any]:
            """Return context that may include sensitive and restricted records.

            This tool is absent from the normal MCP surface. The operator must
            restart the server with PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1 before
            a client can discover or invoke it.
            """
            return deps.get_person_context.execute(
                person_id,
                purpose=purpose,
                max_items=max_items,
                include_sensitive=True,
            ).model_dump(mode="json")

    @mcp.tool(annotations=_READ_ONLY)
    def search_people(query: str, limit: int = 10) -> dict[str, Any]:
        """Free-text search over stored people for browsing or lookup.

        Broader than `resolve_person`: use this to list who is known that matches a
        query, rather than to pin down a single identity. Returns ranked candidates.
        """
        results = deps.search_people.execute(query, limit=limit)
        return {"query": query, "results": [candidate.model_dump(mode="json") for candidate in results]}

    @mcp.tool(annotations=_READ_ONLY)
    def semantic_search(
        query: str,
        kinds: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search active people and safe interaction summaries by multilingual semantic similarity.

        This optional local search requires an explicit `people-context reindex --semantic` first. It never
        downloads a model while serving a query and refuses to mix vectors from different model revisions.
        """
        try:
            return deps.semantic_search.execute(query, kinds=kinds, limit=limit).model_dump(mode="json")
        except SemanticSearchValidationError as exc:
            return {"error": exc.code, "message": str(exc)}

    @mcp.tool(annotations=_WRITE)
    def remember_person(
        name: str,
        aliases: list[dict] | None = None,
        summary: str | None = None,
        is_self: bool = False,
        source: str = "agent",
    ) -> dict[str, Any]:
        """Create a new person or update the existing one matching `name`.

        Use this to durably record someone the user talks about. `aliases` is a list
        of `{value, kind?, lang?, script?}` objects (kinds: nickname, native_script,
        transliteration, handle, former_name, other); new aliases are merged into an
        existing record. Set `summary` to describe who they are, and `is_self=True`
        only for the user themselves. Returns the person and whether it was created.
        """
        alias_inputs = [AliasInput.model_validate(alias) for alias in (aliases or [])]
        data = RememberPersonInput(
            name=name,
            aliases=alias_inputs,
            summary=summary,
            is_self=is_self,
            source=source,
        )
        try:
            result = deps.remember_person.execute(data)
        except AmbiguousPersonError as exc:
            return {
                "error": "ambiguous_person",
                "message": str(exc),
                "candidates": [
                    {"person_id": pid, "canonical_name": cname}
                    for pid, cname in zip(exc.person_ids, exc.names, strict=True)
                ],
            }
        except SelfAlreadyExistsError as exc:
            return {
                "error": "self_already_exists",
                "message": str(exc),
                "existing": {"person_id": exc.existing_id, "canonical_name": exc.existing_name},
            }
        return result.model_dump(mode="json")
