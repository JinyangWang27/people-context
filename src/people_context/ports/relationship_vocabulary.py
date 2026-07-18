"""Ports for relationship vocabulary and canonical relationship storage."""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from people_context.domain.relationship import Relationship
from people_context.domain.relationship_vocabulary import RelationshipType


@runtime_checkable
class RelationshipVocabularyReader(Protocol):
    """Read vocabulary rows, synonyms, and uncategorized types in use."""

    def resolve(self, value: str) -> RelationshipType | None: ...

    def list_types(self) -> list[RelationshipType]: ...

    def list_uncategorized_types(self) -> list[str]: ...


@runtime_checkable
class RelationshipVocabularyWriter(Protocol):
    """Add portable custom vocabulary state."""

    def add(self, rows: list[RelationshipType]) -> None: ...


@runtime_checkable
class RelationshipStore(Protocol):
    """Read and mutate relationship rows for canonicalization and deduplication."""

    def save_relationship(self, relationship: Relationship) -> None: ...

    def find_active_relationship(
        self,
        subject_id: str,
        object_id: str,
        type: str,
        as_of: date,
    ) -> Relationship | None: ...

    def list_relationships(self) -> list[Relationship]: ...

    def delete_relationship(self, relationship_id: str) -> None: ...
