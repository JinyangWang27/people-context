"""Stub tools: the full intended surface, typed, returning `not_implemented`.

Every stub carries the correct MCP annotations (read-only / write / destructive)
and a client-facing docstring, so the complete tool surface is visible in any MCP
client's tool list before each tool is implemented. Each returns a fixed shape:
``{"status": "not_implemented", "planned_milestone": "M2|M3"}``.

Milestones follow docs/mcp-interface.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def _pending(milestone: str) -> dict[str, Any]:
    return {"status": "not_implemented", "planned_milestone": milestone}


def register(mcp: FastMCP) -> None:
    """Register all not-yet-implemented tools with their annotations and schemas."""

    # ---- Read-only stubs -------------------------------------------------

    @mcp.tool(annotations=_READ_ONLY)
    def get_communication_guidance(person_id: str, situation: str | None = None) -> dict[str, Any]:
        """Return structured signal for composing communication advice about a person.

        Bundles the person's traits, relevant relationship/role context, recent
        interaction friction notes, active communication-note reminders, and the
        user's communication-philosophy text. The advice itself is composed by you,
        the client model — the server only supplies context. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_READ_ONLY)
    def list_reminders(
        person_id: str | None = None,
        due_before: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List reminders, optionally filtered by person, due date, or status.

        Pull-based (no server-side scheduler): call this to surface due follow-ups
        and occasions on your own schedule. (Not yet implemented.)
        """
        return _pending("M2")

    # ---- Write stubs -----------------------------------------------------

    @mcp.tool(annotations=_WRITE)
    def add_alias(
        person_id: str,
        value: str,
        kind: str | None = None,
        lang: str | None = None,
        script: str | None = None,
    ) -> dict[str, Any]:
        """Add an alias (nickname, native-script name, transliteration, handle, or
        former name) to an existing person. (Not yet implemented.)
        """
        return _pending("M2")

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
        """Create or update a directed, typed relationship from one person to another
        over an optional validity period. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def set_affiliation(
        person_id: str,
        org: str,
        role: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Create or update a person's role at an organization (id or name) over a
        period. (Not yet implemented.)
        """
        return _pending("M2")

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
        """Record a time-aware fact about a person (predicate + value with an optional
        validity period). (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def record_observation(
        person_id: str,
        text: str,
        observed_at: str | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        """Record a subjective observation about a person, kept explicitly separate
        from facts. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def record_trait(
        person_id: str,
        category: str,
        value: str,
        evidence_note: str | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        """Record a derived characteristic (communication style, temperament, values,
        preference, topics to avoid) with optional supporting evidence.
        (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def record_interaction(
        summary: str,
        participant_ids: list[str],
        occurred_at: str | None = None,
        channel: str | None = None,
        sensitivity: str | None = None,
    ) -> dict[str, Any]:
        """Record a concise interaction summary and its participants — a summary only,
        never a transcript. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def correct_record(entity_type: str, entity_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Correct a previously recorded fact/observation/trait/relationship/
        affiliation without silently overwriting history. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def set_reminder(
        person_id: str,
        text: str,
        kind: str,
        due_at: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Create a reminder for a person (follow-up, occasion, or communication note).
        (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def complete_reminder(reminder_id: str) -> dict[str, Any]:
        """Mark a reminder as completed. (Not yet implemented.)"""
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def set_communication_philosophy(text: str) -> dict[str, Any]:
        """Store or update the user's free-text communication-guidance framework, used
        to frame later communication advice. (Not yet implemented.)
        """
        return _pending("M2")

    @mcp.tool(annotations=_WRITE)
    def import_content(source_type: str, content: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Extract candidate person/alias/fact/interaction records from a source (e.g.
        an .eml/mbox file) into staging. Raw content is parsed in-memory and
        discarded — never persisted. (Not yet implemented.)
        """
        return _pending("M3")

    @mcp.tool(annotations=_WRITE)
    def review_import(batch_id: str) -> dict[str, Any]:
        """Return the staged candidates for an import batch for user review.
        (Not yet implemented.)
        """
        return _pending("M3")

    @mcp.tool(annotations=_WRITE)
    def commit_import(batch_id: str, accepted_ids: list[str]) -> dict[str, Any]:
        """Write the accepted staged candidates into the real tables with provenance
        `source: import/<type>`. (Not yet implemented.)
        """
        return _pending("M3")

    # ---- Destructive stubs ----------------------------------------------

    @mcp.tool(annotations=_DESTRUCTIVE)
    def merge_people(primary_id: str, duplicate_id: str) -> dict[str, Any]:
        """Merge a duplicate person record into a primary one, re-parenting all related
        rows and keeping a full audit trail. Destructive and irreversible.
        (Not yet implemented.)
        """
        return _pending("M3")

    @mcp.tool(annotations=_DESTRUCTIVE)
    def forget(target: str, scope: str) -> dict[str, Any]:
        """Hard-delete a target (a person or a narrower scope) and write a tombstone
        audit entry. Destructive and irreversible. (Not yet implemented.)
        """
        return _pending("M3")

    # export_data is neither read-only nor destructive: it returns the full
    # dataset (maximal disclosure), so clients should still gate it for approval.
    @mcp.tool(annotations=_WRITE)
    def export_data() -> dict[str, Any]:
        """Export the full dataset as a JSON document for portability.

        This is maximal disclosure — it returns every stored record, so it is
        deliberately annotated as a non-read-only tool that clients should gate
        behind approval. (Not yet implemented.)
        """
        return _pending("M3")
