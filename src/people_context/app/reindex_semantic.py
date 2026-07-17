"""Explicit, atomic rebuild of the optional semantic index."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.ports.semantic import (
    EmbeddingProvider,
    SemanticDocumentReader,
    SemanticIndexMetadata,
    SemanticIndexRebuilder,
)


class ReindexSemanticResult(BaseModel):
    """Counts and model identity from one semantic rebuild."""

    entities: int
    people: int
    interactions: int
    model_id: str
    dimension: int


class ReindexSemantic:
    """Embed eligible documents, then replace vectors and metadata together."""

    def __init__(
        self,
        documents: SemanticDocumentReader,
        provider: EmbeddingProvider,
        rebuilder: SemanticIndexRebuilder,
    ) -> None:
        self._documents = documents
        self._provider = provider
        self._rebuilder = rebuilder

    def execute(self) -> ReindexSemanticResult:
        documents = self._documents.list_documents()
        vectors = self._provider.embed([document.text for document in documents]) if documents else []
        if any(len(vector) != self._provider.dimension for vector in vectors):
            raise ValueError("embedding provider returned an unexpected vector dimension")
        metadata = SemanticIndexMetadata(
            model_id=self._provider.model_id,
            dimension=self._provider.dimension,
        )
        self._rebuilder.replace_all(documents, vectors, metadata)
        return ReindexSemanticResult(
            entities=len(documents),
            people=sum(document.kind == "person" for document in documents),
            interactions=sum(document.kind == "interaction" for document in documents),
            model_id=metadata.model_id,
            dimension=metadata.dimension,
        )
