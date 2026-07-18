"""Shared write-use-case errors, provenance construction, and audit helpers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from pydantic import BaseModel

from people_context.domain.shared import Provenance, new_id
from people_context.ports.audit_log import AuditEntry, AuditLog
from people_context.ports.changelog import Changelog, ChangelogEntry
from people_context.ports.clock import Clock
from people_context.ports.hlc import HybridLogicalClock
from people_context.ports.repository import PersonReader
from people_context.ports.unit_of_work import NullUnitOfWork, UnitOfWork

_P = ParamSpec("_P")
_R = TypeVar("_R")


def transactional(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run a write-use-case method inside its configured unit of work."""

    @wraps(method)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        instance = args[0]
        uow = cast(UnitOfWork, instance._uow)
        with uow:
            return method(*args, **kwargs)

    return wrapped


def unit_of_work_for(*dependencies: object) -> UnitOfWork:
    """Return the first adapter-provided UoW, or a no-op boundary for port fakes."""
    for dependency in dependencies:
        candidate = getattr(dependency, "unit_of_work", None)
        if candidate is not None:
            return cast(UnitOfWork, candidate)
    return NullUnitOfWork()


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
    replay_payload: dict[str, Any] | None = None,
    changed_fields: list[str] | None = None,
    op_kind: str | None = None,
    transaction_id: str | None = None,
    session: str | None = None,
    stated_by: str | None = None,
    changelog_entity_type: str | None = None,
    changelog_entity_id: str | None = None,
) -> str:
    """Append accountability and full replay records through one application seam."""
    transaction_id = transaction_id or new_id()
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
    changelog_mutation(
        audit,
        clock,
        entity_type=changelog_entity_type or entity_type,
        entity_id=changelog_entity_id or entity_id,
        op_kind=op_kind or op,
        payload=replay_payload if replay_payload is not None else payload,
        changed_fields=changed_fields if changed_fields is not None else _payload_fields(payload),
        transaction_id=transaction_id,
        source=source,
        session=session,
        stated_by=stated_by,
    )
    return transaction_id


def changelog_mutation(
    audit: AuditLog,
    clock: Clock,
    *,
    entity_type: str,
    entity_id: str,
    op_kind: str,
    payload: dict[str, Any],
    changed_fields: list[str],
    transaction_id: str,
    source: str,
    session: str | None = None,
    stated_by: str | None = None,
) -> None:
    """Append a replay-only child operation when the adapter exposes sync foundations.

    The seam is capability-discovered: an audit adapter opts in by exposing both
    ``changelog`` and ``hybrid_clock``. Wrappers around a real audit adapter MUST
    forward both attributes, or replay history is silently dropped; forwarding only
    one is always a wiring mistake and is reported loudly below.
    """
    changelog = cast(Changelog | None, getattr(audit, "changelog", None))
    hybrid_clock = cast(HybridLogicalClock | None, getattr(audit, "hybrid_clock", None))
    if changelog is None and hybrid_clock is None:
        return
    if changelog is None or hybrid_clock is None:
        raise RuntimeError(
            "audit adapter exposes only one of changelog/hybrid_clock; "
            "a wrapper must forward both sync-foundation attributes"
        )
    hlc = hybrid_clock.tick()
    actor = {"source": source}
    if session is not None:
        actor["session"] = session
    if stated_by is not None:
        actor["stated_by"] = stated_by
    changelog.append(
        ChangelogEntry(
            device_id=hybrid_clock.device_id,
            hlc_physical_ms=hlc.physical_ms,
            hlc_logical=hlc.logical_counter,
            transaction_id=transaction_id,
            entity_type=entity_type,
            entity_id=entity_id,
            op_kind=op_kind,
            payload=payload,
            changed_fields=sorted(set(changed_fields)),
            actor=actor,
            inserted_at=clock.now(),
        )
    )


def _payload_fields(payload: dict[str, Any]) -> list[str]:
    fields = payload.get("fields", [])
    return [str(field) for field in fields] if isinstance(fields, list) else []


def snapshot(model: BaseModel) -> dict[str, Any]:
    """Return a JSON-compatible audit snapshot."""
    return model.model_dump(mode="json")
