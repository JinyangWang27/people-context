"""Pure relationship vocabulary normalization and perspective-rendering policy."""

from __future__ import annotations

from people_context.domain.relationship_vocabulary import (
    NormalizedRelationship,
    RelationshipType,
    normalize_relationship_type,
)
from people_context.ports.relationship_vocabulary import RelationshipVocabularyReader


class EmptyRelationshipVocabulary:
    """Fallback vocabulary for port fakes and legacy composition."""

    def resolve(self, value: str) -> RelationshipType | None:
        return None

    def list_types(self) -> list[RelationshipType]:
        return []

    def list_uncategorized_types(self) -> list[str]:
        return []


def normalize_relationship(
    subject_id: str,
    object_id: str,
    value: str,
    vocabulary: RelationshipVocabularyReader,
) -> NormalizedRelationship:
    """Resolve synonyms and canonical direction for one relationship assertion."""
    normalized = normalize_relationship_type(value)
    row = vocabulary.resolve(normalized)
    if row is None:
        return NormalizedRelationship(
            subject_id=subject_id,
            object_id=object_id,
            type=normalized,
            category="uncategorized",
        )
    if not row.canonical:
        return NormalizedRelationship(
            subject_id=object_id,
            object_id=subject_id,
            type=row.inverse or row.type,
            category=row.category,
        )
    if row.symmetric and object_id < subject_id:
        subject_id, object_id = object_id, subject_id
    return NormalizedRelationship(
        subject_id=subject_id,
        object_id=object_id,
        type=row.type,
        category=row.category,
        symmetric=row.symmetric,
    )


def relationship_display_type(
    stored_type: str,
    *,
    queried_person_id: str,
    subject_id: str,
    vocabulary: RelationshipVocabularyReader,
) -> str:
    """Render a canonical edge from one endpoint's perspective."""
    row = vocabulary.resolve(stored_type)
    if row is None or row.symmetric or queried_person_id == subject_id:
        return stored_type
    return row.inverse or stored_type
