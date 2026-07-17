"""Correct an assertion in place while preserving before/after in audit."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError

from people_context.app.write_support import (
    InvalidCorrectionError,
    RecordNotFoundError,
    audit_mutation,
    require_active_person,
    snapshot,
)
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder
from people_context.domain.trait import Trait
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import Record, RecordReader, RecordWriter
from people_context.ports.repository import PersonReader

_CORRECTABLE_FIELDS: dict[str, set[str]] = {
    "fact": {"predicate", "value", "valid_from", "valid_to", "confidence", "sensitivity"},
    "observation": {"text", "observed_at", "sensitivity"},
    "trait": {"category", "value", "evidence_note", "confidence", "sensitivity"},
    "relationship": {"type", "label", "valid_from", "valid_to", "confidence"},
    "affiliation": {"role", "valid_from", "valid_to", "confidence"},
    "reminder": {"text", "kind", "due_at", "recurrence"},
}
_PERIOD_TYPES = (Fact, Relationship, Affiliation)


class CorrectRecordInput(BaseModel):
    """Input for an in-place correction."""

    entity_type: str
    entity_id: str
    fields: dict[str, Any]
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class CorrectRecord:
    """Validate, update, and audit supported assertion fields."""

    def __init__(
        self,
        records: RecordReader,
        writer: RecordWriter,
        audit: AuditLog,
        clock: Clock,
        *,
        people: PersonReader,
    ) -> None:
        self._records = records
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._people = people

    def execute(self, data: CorrectRecordInput) -> Record:
        """Correct one record in place with a lossless audit snapshot."""
        allowed = _CORRECTABLE_FIELDS.get(data.entity_type)
        invalid = sorted(data.fields.keys() - (allowed or set()))
        if allowed is None or not data.fields or invalid:
            raise InvalidCorrectionError(data.entity_type, invalid or sorted(data.fields), sorted(allowed or set()))
        current = self._records.get_record(data.entity_type, data.entity_id)
        if current is None:
            raise RecordNotFoundError(data.entity_type, data.entity_id)
        for person_id in _linked_person_ids(current):
            require_active_person(self._people, person_id)
        try:
            validated_fields = _validate_fields(current, data.fields)
        except (ValidationError, ValueError, TypeError):
            raise InvalidCorrectionError(data.entity_type, sorted(data.fields), sorted(allowed)) from None
        persistence_fields = dict(validated_fields)
        if isinstance(current, Trait):
            persistence_fields["updated_at"] = self._clock.now()
        updated = self._writer.update_record_fields(data.entity_type, data.entity_id, persistence_fields)
        if updated is None:
            raise RecordNotFoundError(data.entity_type, data.entity_id)
        audit_mutation(
            self._audit,
            self._clock,
            op="correct",
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            payload={"before": snapshot(current), "after": snapshot(updated), "fields": sorted(data.fields)},
            source=data.source,
        )
        return updated


def _validate_fields(current: Record, fields: dict[str, Any]) -> dict[str, Any]:
    data = current.model_dump()
    persistence_fields = dict(fields)
    if isinstance(current, _PERIOD_TYPES):
        period_data = current.period.model_dump()
        for field in ("valid_from", "valid_to"):
            if field in persistence_fields:
                period_data[field] = persistence_fields[field]
        data["period"] = period_data
        data.update({key: value for key, value in persistence_fields.items() if key not in period_data})
    else:
        data.update(persistence_fields)
    validated = type(current).model_validate(data)
    validated_data = validated.model_dump()
    result: dict[str, Any] = {}
    for field in fields:
        if field in ("valid_from", "valid_to") and isinstance(validated, _PERIOD_TYPES):
            value = getattr(validated.period, field)
            result[field] = date.fromisoformat(value) if isinstance(value, str) else value
        else:
            result[field] = validated_data[field]
    return result


def _linked_person_ids(record: Record) -> list[str]:
    if isinstance(record, Relationship):
        return [record.subject_id, record.object_id]
    if isinstance(record, Interaction):
        return record.participant_ids
    if isinstance(record, (Fact, Observation, Trait, Affiliation, Reminder)):
        return [record.person_id]
    return []
