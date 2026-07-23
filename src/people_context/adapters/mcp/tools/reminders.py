"""MCP tools for listing, creating, and completing reminders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations
from pydantic import ValidationError

from people_context.adapters.mcp.tools.tool_errors import call_action
from people_context.app.records import CompleteReminderInput, ListRemindersInput, SetReminderInput
from people_context.domain.reminder import ReminderStatus

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def register(mcp: FastMCP, deps: RuntimeUseCases) -> None:
    """Register reminder tools with their locked schemas."""

    @mcp.tool(annotations=_READ_ONLY)
    def list_reminders(
        person_id: str | None = None,
        due_before: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List pull-based reminders, due-dated first and communication notes last."""
        try:
            data = ListRemindersInput(
                person_id=person_id,
                due_before=due_before,
                status=status if status is not None else ReminderStatus.ACTIVE,
            )
        except ValidationError as exc:
            return {"error": "validation_error", "message": str(exc), "details": exc.errors(include_url=False)}
        return {"reminders": [item.model_dump(mode="json") for item in deps.list_reminders.execute(data)]}

    @mcp.tool(annotations=_WRITE)
    def set_reminder(
        person_id: str,
        text: str,
        kind: str,
        due_at: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Create a kind-validated reminder for an existing person."""
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
    def complete_reminder(reminder_id: str) -> dict[str, Any]:
        """Transition one active reminder to completed."""
        return call_action(lambda: deps.complete_reminder.execute(CompleteReminderInput(reminder_id=reminder_id)))
