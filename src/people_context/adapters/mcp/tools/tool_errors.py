"""Shared application-error mapping for MCP mutation tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
