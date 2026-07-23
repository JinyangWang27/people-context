"""Shared application-error mapping for MCP mutation tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from people_context.app.records import (
    InvalidCorrectionError,
    InvalidReminderError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
    ReminderNotActiveError,
)


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
        return {"error": "validation_error", "message": str(exc), "details": exc.errors(include_url=False)}
