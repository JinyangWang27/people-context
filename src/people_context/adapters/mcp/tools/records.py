"""MCP tools for aliases, relationships, and assertive records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.tool_errors import call_action, flag_refusals
from people_context.app.people import AddAliasInput
from people_context.app.records import (
    CorrectRecordInput,
    RecordFactInput,
    RecordInteractionInput,
    RecordObservationInput,
    RecordTraitInput,
    SetAffiliationInput,
    SupersedeFactInput,
)
from people_context.app.relationships import SetRelationshipInput
from people_context.domain.person import AliasKind
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register record-oriented write tools with their locked schemas."""

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def add_alias(
        person_id: str,
        value: str,
        kind: AliasKind | None = None,
        lang: str | None = None,
        script: str | None = None,
    ) -> dict[str, Any]:
        """Add a normalized-deduplicated alias to an existing person.

        `kind` is one of `nickname`, `native_script`, `transliteration`, `handle`, `former_name`, or
        `other`, and defaults to `other`. The published schema carries the enum, so an unlisted value
        is refused before the alias is built rather than dropped.
        """
        return call_action(
            lambda: deps.add_alias.execute(
                AddAliasInput(
                    person_id=person_id,
                    value=value,
                    kind=kind if kind is not None else AliasKind.OTHER,
                    lang=lang,
                    script=script,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def set_relationship(
        subject_id: str,
        object_id: str,
        type: str,
        label: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Create a directed relationship between two existing people."""
        return call_action(
            lambda: deps.set_relationship.execute(
                SetRelationshipInput(
                    subject_id=subject_id,
                    object_id=object_id,
                    type=type,
                    label=label,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    confidence=confidence,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def set_affiliation(
        person_id: str,
        org: str,
        role: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Create an affiliation, resolving an org id or get/creating by name."""
        return call_action(
            lambda: deps.set_affiliation.execute(
                SetAffiliationInput(
                    person_id=person_id,
                    org=org,
                    role=role,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    confidence=confidence,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def record_fact(
        person_id: str,
        predicate: str,
        value: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Record a time-aware fact about an existing person.

        `sensitivity` defaults to `personal`; `sensitive` and `restricted` records are withheld from
        ordinary reads. Prefer `remember` for a single statement named by a person's name.
        """
        return call_action(
            lambda: deps.record_fact.execute(
                RecordFactInput(
                    person_id=person_id,
                    predicate=predicate,
                    value=value,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    confidence=confidence,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def record_observation(
        person_id: str,
        text: str,
        observed_at: str | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Record a subjective observation, separate from disclosed context."""
        return call_action(
            lambda: deps.record_observation.execute(
                RecordObservationInput(
                    person_id=person_id,
                    text=text,
                    observed_at=observed_at,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def record_trait(
        person_id: str,
        category: TraitCategory,
        value: str,
        evidence_note: str | None = None,
        confidence: float | None = None,
        sensitivity: Sensitivity | None = None,
        evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a derived trait with validated category and provenance.

        `category` is one of `communication_style`, `temperament`, `values`, `preference`,
        `topics_to_avoid`, or `other`. Cite the observation or interaction it rests on in
        `evidence_note` or `evidence_ids` where you can.
        """
        return call_action(
            lambda: deps.record_trait.execute(
                RecordTraitInput(
                    person_id=person_id,
                    category=category,
                    value=value,
                    evidence_note=evidence_note,
                    confidence=confidence,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                    evidence_ids=list(evidence_ids or []),
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def record_interaction(
        summary: str,
        participant_ids: list[str],
        occurred_at: str | None = None,
        channel: str | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Record a concise interaction summary after validating all participants."""
        return call_action(
            lambda: deps.record_interaction.execute(
                RecordInteractionInput(
                    summary=summary,
                    participant_ids=participant_ids,
                    occurred_at=occurred_at,
                    channel=channel,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def correct_record(entity_type: str, entity_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Correct whitelisted assertion fields in place with before/after audit."""
        return call_action(
            lambda: deps.correct_record.execute(
                CorrectRecordInput(entity_type=entity_type, entity_id=entity_id, fields=fields)
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def supersede_fact(
        fact_id: str,
        new_value: str,
        effective_from: str,
        confidence: float | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Close a fact that was true and open its replacement from an effective date.

        Use this when a stored value was historically correct and the real-world state changed;
        `correct_record` remains the tool for a value that was simply wrong. The old fact keeps its
        person, predicate, value, and provenance and is closed the day before `effective_from`; the
        replacement inherits the old assertion's original end date, so a bounded claim is never
        widened into an open-ended one. Person, predicate, and the replacement's end date cannot be
        changed here. Omitting `confidence` or `sensitivity` inherits the old fact's.

        Both rows commit together under one logical transaction, or neither commits.
        """
        return call_action(
            lambda: deps.supersede_fact.execute(
                SupersedeFactInput(
                    fact_id=fact_id,
                    new_value=new_value,
                    effective_from=effective_from,
                    confidence=confidence,
                    sensitivity=sensitivity,
                )
            )
        )
