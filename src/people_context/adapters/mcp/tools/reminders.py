"""MCP tools for listing, creating, and completing reminders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations
from pydantic import ValidationError

from people_context.adapters.mcp.tools.references import resolve_reference
from people_context.adapters.mcp.tools.tool_errors import call_action, flag_refusals, validation_error_payload
from people_context.app.records import CompleteReminderInput, ListRemindersInput, SetReminderInput
from people_context.domain.reminder import ReminderKind, ReminderStatus

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(read_only_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register reminder tools with their locked schemas."""

    @mcp.tool(annotations=_READ_ONLY)
    @flag_refusals
    async def list_reminders(
        person_id: str | None = None,
        due_before: str | None = None,
        status: str | None = None,
        person: str | None = None,
    ) -> dict[str, Any]:
        """List pull-based reminders, due-dated first and communication notes last.

        Filter by `person_id`, or by `person` (a name or alias) resolved inline; omit both for
        every person's reminders.
        """
        # `is not None`, not truthiness: an explicitly empty name is a caller mistake, and treating
        # it as "no filter given" would answer a request scoped to one person with everyone's rows.
        if person is not None and person_id is None:
            target = resolve_reference(deps, person_id=None, person=person)
            if isinstance(target, dict):
                return target
            person_id = target
        try:
            data = ListRemindersInput(
                person_id=person_id,
                due_before=due_before,
                status=status if status is not None else ReminderStatus.ACTIVE,
            )
        except ValidationError as exc:
            return validation_error_payload(exc)
        return {"reminders": [item.model_dump(mode="json") for item in deps.list_reminders.execute(data)]}

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def set_reminder(
        person_id: str,
        text: str,
        kind: ReminderKind,
        due_at: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Create a reminder for an existing person.

        `kind` is `follow_up` (dated, something to do), `occasion` (a date that recurs), or
        `communication_note` (undated guidance surfaced with the person's context).
        """
        return call_action(
            lambda: deps.set_reminder.execute(
                SetReminderInput(
                    person_id=person_id,
                    text=text,
                    kind=kind,
                    due_at=due_at,
                    recurrence=recurrence,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def complete_reminder(reminder_id: str) -> dict[str, Any]:
        """Transition one active reminder to completed."""
        return call_action(lambda: deps.complete_reminder.execute(CompleteReminderInput(reminder_id=reminder_id)))
