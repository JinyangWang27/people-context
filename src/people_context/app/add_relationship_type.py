"""Add custom relationship vocabulary through the audited write seam."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from people_context.app.write_support import audit_mutation, transactional, unit_of_work_for
from people_context.domain.relationship_vocabulary import RelationshipType, normalize_relationship_type
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.relationship_vocabulary import RelationshipVocabularyReader, RelationshipVocabularyWriter


class AddRelationshipTypeInput(BaseModel):
    """Input for one custom vocabulary type or inverse pair."""

    type: str
    category: str
    inverse: str | None = None
    symmetric: bool = False
    synonyms: list[str] = Field(default_factory=list)
    source: str = "cli"
    session: str | None = None
    stated_by: str | None = None

    @model_validator(mode="after")
    def _validate_direction(self) -> AddRelationshipTypeInput:
        if self.symmetric and self.inverse is not None:
            raise ValueError("--inverse and --symmetric are mutually exclusive")
        return self


class RelationshipTypeAlreadyExistsError(Exception):
    """Raised when add-only vocabulary would replace an existing row or synonym."""


class AddRelationshipType:
    """Persist custom vocabulary atomically with audit and changelog capture."""

    def __init__(
        self,
        reader: RelationshipVocabularyReader,
        writer: RelationshipVocabularyWriter,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: AddRelationshipTypeInput) -> list[RelationshipType]:
        type_name = normalize_relationship_type(data.type)
        category = normalize_relationship_type(data.category)
        inverse = normalize_relationship_type(data.inverse) if data.inverse else None
        synonyms = sorted(
            {normalized for value in data.synonyms if (normalized := normalize_relationship_type(value))}
        )
        candidates = [type_name, *synonyms]
        if inverse is not None:
            candidates.append(inverse)
        if not type_name or not category or (data.inverse is not None and not inverse):
            raise ValueError("type, category, and inverse must contain at least one word character")
        if len(set(candidates)) != len(candidates):
            raise ValueError("type, inverse, and synonyms must be distinct")
        if any(self._reader.resolve(candidate) is not None for candidate in candidates):
            raise RelationshipTypeAlreadyExistsError("relationship type or synonym already exists")
        rows = [
            RelationshipType(
                type=type_name,
                inverse=inverse,
                symmetric=data.symmetric,
                category=category,
                canonical=True,
                synonyms=synonyms,
            )
        ]
        if inverse is not None:
            rows.append(
                RelationshipType(
                    type=inverse,
                    inverse=type_name,
                    symmetric=False,
                    category=category,
                    canonical=False,
                )
            )
        self._writer.add(rows)
        payload = {"rows": [row.model_dump(mode="json") for row in rows]}
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="relationship_type",
            entity_id=type_name,
            payload=payload,
            replay_payload=payload,
            changed_fields=["type", "inverse", "symmetric", "category", "canonical", "synonyms"],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return rows
