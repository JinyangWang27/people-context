"""Shared application-error mapping and refusal flagging for MCP tools."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec

import pydantic_core
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ValidationError

from people_context.app.records import (
    InvalidCorrectionError,
    InvalidReminderError,
    InvalidSupersessionError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
    ReminderNotActiveError,
)

_ToolPayload = dict[str, Any]
_P = ParamSpec("_P")


def flag_refusals(
    tool: Callable[_P, Awaitable[_ToolPayload]],
) -> Callable[_P, Awaitable[_ToolPayload | CallToolResult]]:
    """Report a refusal payload as an MCP tool error while keeping the payload intact.

    Every tool answers a refusal with a structured ``{"error": <code>, ...}`` payload rather than a
    protocol error, so the model reads an actionable reason instead of a stack trace. Returned as an
    ordinary result it leaves ``isError`` false, and a client applying the standard MCP check reads a
    dropped write as a successful one. Only the refusal branch is wrapped: the payload still ships as
    ``structuredContent`` and as the same JSON text block the SDK would have rendered, now with
    ``isError`` set so both kinds of caller agree on what happened.

    The wrapper is registered in place of the tool, so ``functools.wraps`` matters twice over:
    ``inspect.signature`` unwraps it to derive the published input schema from the real signature,
    and the description the model reads is the tool's own docstring.
    """

    @functools.wraps(tool)
    async def flagged(*args: _P.args, **kwargs: _P.kwargs) -> _ToolPayload | CallToolResult:
        payload = await tool(*args, **kwargs)
        if not payload.get("error"):
            return payload
        # Mirrors the SDK's own dict-to-text rendering, so flagging a refusal leaves the text block
        # a client already displays for it unchanged.
        text = pydantic_core.to_json(payload, fallback=str, indent=2).decode()
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=payload,
            is_error=True,
        )

    return flagged


def validation_error_payload(exc: ValidationError) -> dict[str, Any]:
    """Render a rejected argument as a payload the transport can actually serialize.

    ``errors()`` carries a ``ctx`` holding the original exception object whenever a field validator
    raised one, and that is not JSON. Left in, the tool result fails to serialize and the caller gets
    a protocol error in place of the structured refusal this function exists to give them.
    """
    return {
        "error": "validation_error",
        "message": str(exc),
        "details": exc.errors(include_url=False, include_context=False),
    }


def call_action(action: Callable[[], BaseModel]) -> dict[str, Any]:
    """Execute one use case and map stable application errors to tool payloads."""
    try:
        return action().model_dump(mode="json")
    except PersonNotFoundError as exc:
        return {"error": "person_not_found", "message": str(exc), "person_id": exc.person_id}
    except OrganizationNotFoundError as exc:
        return {"error": "organization_not_found", "message": str(exc), "org_id": exc.org_id}
    except RecordNotFoundError as exc:
        return {
            "error": "record_not_found",
            "message": str(exc),
            "entity_type": exc.entity_type,
            "entity_id": exc.entity_id,
        }
    except InvalidCorrectionError as exc:
        return {
            "error": "invalid_correction",
            "message": str(exc),
            "entity_type": exc.entity_type,
            "fields": exc.fields,
            "allowed_fields": exc.allowed_fields,
        }
    except InvalidSupersessionError as exc:
        # The reason is a stable machine code naming which temporal rule the date broke; it carries
        # no stored value, so an agent can explain the refusal without reading the fact back.
        return {
            "error": "invalid_supersession",
            "message": str(exc),
            "fact_id": exc.fact_id,
            "reason": exc.reason,
        }
    except ReminderNotActiveError as exc:
        return {
            "error": "reminder_not_active",
            "message": str(exc),
            "reminder_id": exc.reminder_id,
            "status": exc.status,
        }
    except InvalidReminderError as exc:
        return {"error": "invalid_reminder", "message": str(exc)}
    except ValidationError as exc:
        return validation_error_payload(exc)
