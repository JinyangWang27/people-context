"""Best-effort incremental maintenance for derived semantic vectors."""

from __future__ import annotations

from people_context.domain.interaction import Interaction
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.ports.semantic import EmbeddingProvider, VectorIndex


class SemanticIndexUpdater:
    """Derive and refresh one entity vector after a successful primary write."""

    def __init__(self, provider: EmbeddingProvider, index: VectorIndex) -> None:
        self._provider = provider
        self._index = index

    def refresh_person(self, person: Person) -> None:
        if person.deleted_at is not None:
            self._index.delete(person.id)
            return
        parts = [person.canonical_name, *(alias.value for alias in person.aliases)]
        if person.summary:
            parts.append(person.summary)
        self._upsert("person", person.id, "\n".join(parts))

    def refresh_interaction(self, interaction: Interaction) -> None:
        if interaction.sensitivity not in {Sensitivity.PUBLIC, Sensitivity.PERSONAL}:
            self._index.delete(interaction.id)
            return
        self._upsert("interaction", interaction.id, interaction.summary)

    def delete(self, entity_id: str) -> None:
        self._index.delete(entity_id)

    def _upsert(self, kind: str, entity_id: str, text: str) -> None:
        vectors = self._provider.embed([text])
        if len(vectors) != 1 or len(vectors[0]) != self._provider.dimension:
            raise ValueError("embedding provider returned an unexpected vector shape")
        self._index.upsert(kind, entity_id, vectors[0])
