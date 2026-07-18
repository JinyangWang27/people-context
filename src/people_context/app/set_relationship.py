"""Create or update one canonical relationship between two known people."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from people_context.app.relationship_policy import EmptyRelationshipVocabulary, normalize_relationship
from people_context.app.write_support import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.relationship import Relationship
from people_context.domain.shared import Confidence, ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.relationship_vocabulary import RelationshipStore, RelationshipVocabularyReader
from people_context.ports.repository import PersonReader


class SetRelationshipInput(BaseModel):
    """Input for a relationship assertion in either vocabulary direction."""

    subject_id: str
    object_id: str
    type: str
    label: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SetRelationship:
    """Normalize, deduplicate, persist, and audit one relationship assertion."""

    def __init__(
        self,
        people: PersonReader,
        writer: RelationshipStore,
        audit: AuditLog,
        clock: Clock,
        vocabulary: RelationshipVocabularyReader | None = None,
    ) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._vocabulary = vocabulary or EmptyRelationshipVocabulary()
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: SetRelationshipInput) -> Relationship:
        """Create a canonical edge, or update the matching active edge in place."""
        for person_id in (data.subject_id, data.object_id):
            require_active_person(self._people, person_id)
        normalized = normalize_relationship(data.subject_id, data.object_id, data.type, self._vocabulary)
        if not normalized.type:
            raise ValueError("relationship type must contain at least one word character")
        finder = getattr(self._writer, "find_active_relationship", None)
        existing = (
            finder(
                normalized.subject_id,
                normalized.object_id,
                normalized.type,
                self._clock.now().date(),
            )
            if finder is not None
            else None
        )
        if existing is not None:
            updated, changed_fields = self._updated_relationship(existing, data)
            self._writer.save_relationship(updated)
            audit_mutation(
                self._audit,
                self._clock,
                op="update",
                entity_type="relationship",
                entity_id=updated.id,
                payload={"before": snapshot(existing), "after": snapshot(updated)},
                replay_payload=snapshot(updated),
                changed_fields=changed_fields,
                source=data.source,
                session=data.session,
                stated_by=data.stated_by,
            )
            return updated
        period = ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to)
        confidence = data.confidence if data.confidence is not None else 1.0
        relationship = Relationship(
            subject_id=normalized.subject_id,
            object_id=normalized.object_id,
            type=normalized.type,
            label=data.label,
            period=period,
            confidence=confidence,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._writer.save_relationship(relationship)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="relationship",
            entity_id=relationship.id,
            payload=snapshot(relationship),
            replay_payload=snapshot(relationship),
            changed_fields=[
                "subject_id",
                "object_id",
                "type",
                "label",
                "valid_from",
                "valid_to",
                "confidence",
                "provenance",
                "created_at",
            ],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return relationship

    @staticmethod
    def _updated_relationship(
        existing: Relationship,
        data: SetRelationshipInput,
    ) -> tuple[Relationship, list[str]]:
        updates: dict[str, object] = {}
        changed_fields: list[str] = []

        if data.label is not None and data.label != existing.label:
            updates["label"] = data.label
            changed_fields.append("label")

        valid_from = existing.period.valid_from
        valid_to = existing.period.valid_to
        period_changed = False
        if data.valid_from is not None and data.valid_from != valid_from:
            valid_from = data.valid_from
            period_changed = True
            changed_fields.append("valid_from")
        if data.valid_to is not None and data.valid_to != valid_to:
            valid_to = data.valid_to
            period_changed = True
            changed_fields.append("valid_to")
        if period_changed:
            updates["period"] = ValidityPeriod(valid_from=valid_from, valid_to=valid_to)

        if data.confidence is not None and data.confidence != existing.confidence:
            updates["confidence"] = data.confidence
            changed_fields.append("confidence")

        new_provenance = provenance(data.source, data.session, data.stated_by)
        if new_provenance != existing.provenance:
            updates["provenance"] = new_provenance
            changed_fields.append("provenance")

        return existing.model_copy(update=updates), changed_fields
