"""MCP tools for aliases, relationships, and assertive records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.tool_errors import call_action
from people_context.app.people import AddAliasInput
from people_context.app.records import (
    CorrectRecordInput,
    RecordFactInput,
    RecordInteractionInput,
    RecordObservationInput,
    RecordTraitInput,
    SetAffiliationInput,
)
from people_context.app.relationships import SetRelationshipInput
from people_context.domain.person import AliasKind
from people_context.domain.shared import Sensitivity

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register record-oriented write tools with their locked schemas."""

    @mcp.tool(annotations=_WRITE)
    def add_alias(
        person_id: str,
        value: str,
        kind: str | None = None,
        lang: str | None = None,
        script: str | None = None,
    ) -> dict[str, Any]:
        """Add a normalized-deduplicated alias to an existing person."""
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
    def set_relationship(
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
    def set_affiliation(
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
    def record_fact(
        person_id: str,
        predicate: str,
        value: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        """Record a time-aware fact about an existing person."""
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
    def record_observation(
        person_id: str,
        text: str,
        observed_at: str | None = None,
        sensitivity: str | None = None,
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
    def record_trait(
        person_id: str,
        category: str,
        value: str,
        evidence_note: str | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        """Record a derived trait with validated category and provenance."""
        return call_action(
            lambda: deps.record_trait.execute(
                RecordTraitInput(
                    person_id=person_id,
                    category=category,
                    value=value,
                    evidence_note=evidence_note,
                    confidence=confidence,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    def record_interaction(
        summary: str,
        participant_ids: list[str],
        occurred_at: str | None = None,
        channel: str | None = None,
        sensitivity: str | None = None,
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
    def correct_record(entity_type: str, entity_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Correct whitelisted assertion fields in place with before/after audit."""
        return call_action(
            lambda: deps.correct_record.execute(
                CorrectRecordInput(entity_type=entity_type, entity_id=entity_id, fields=fields)
            )
        )
