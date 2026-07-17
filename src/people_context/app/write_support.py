"""Shared write-use-case errors, provenance construction, and audit helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from people_context.domain.shared import Provenance
from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader


class PersonNotFoundError(Exception):
    """Raised when a write targets an unknown or deleted person."""

    def __init__(self, person_id: str) -> None:
        self.person_id = person_id
        super().__init__(f"person not found: {person_id}")


class OrganizationNotFoundError(Exception):
    """Raised when an affiliation names an organization id that does not exist."""

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        super().__init__(f"organization not found: {org_id}")


class RecordNotFoundError(Exception):
    """Raised when a curation write targets an unknown record."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} not found: {entity_id}")


class InvalidCorrectionError(Exception):
    """Raised when correction fields are unsupported or invalid."""

    def __init__(self, entity_type: str, fields: list[str], allowed_fields: list[str]) -> None:
        self.entity_type = entity_type
        self.fields = fields
        self.allowed_fields = allowed_fields
        super().__init__(f"invalid correction fields for {entity_type}: {', '.join(fields)}")


class ReminderNotActiveError(Exception):
    """Raised when a completed/cancelled reminder is completed again."""

    def __init__(self, reminder_id: str, status: str) -> None:
        self.reminder_id = reminder_id
        self.status = status
        super().__init__(f"reminder is not active: {reminder_id} ({status})")


class InvalidReminderError(Exception):
    """Raised when reminder kind and scheduling fields conflict."""


def require_active_person(people: PersonReader, person_id: str):
    """Return an active person or raise the common not-found error."""
    person = people.get(person_id)
    if person is None or person.deleted_at is not None:
        raise PersonNotFoundError(person_id)
    return person


def provenance(source: str, session: str | None, stated_by: str | None) -> Provenance:
    """Build required provenance for an assertive record."""
    return Provenance(source=source, session=session, stated_by=stated_by)


def audit_mutation(
    audit: AuditLog,
    clock: Clock,
    *,
    op: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    source: str,
) -> None:
    """Append one audit entry for one mutated row."""
    audit.append(
        AuditEntry(
            ts=clock.now(),
            op=op,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            source=source,
        )
    )


def snapshot(model: BaseModel) -> dict[str, Any]:
    """Return a JSON-compatible audit snapshot."""
    return model.model_dump(mode="json")
