"""Narrow ports for optional semantic embeddings and derived vector storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SemanticDocument:
    """One entity's derived, indexable text."""

    kind: str
    entity_id: str
    text: str


@dataclass(frozen=True)
class VectorSearchHit:
    """One vector match with the adapter's distance value."""

    entity_id: str
    distance: float


@dataclass(frozen=True)
class SemanticIndexMetadata:
    """Embedding identity stored alongside a derived semantic index."""

    model_id: str
    dimension: int


@dataclass(frozen=True)
class SemanticEntity:
    """Minimal safe hydration for one active semantic-search entity."""

    kind: str
    entity_id: str
    title: str
    summary: str


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Create fixed-size embeddings for text."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorIndex(Protocol):
    """Incrementally maintain and query derived entity vectors."""

    def upsert(self, kind: str, entity_id: str, vector: list[float]) -> None: ...

    def delete(self, entity_id: str) -> None: ...

    def search(self, kind: str, vector: list[float], limit: int) -> list[VectorSearchHit]: ...


@runtime_checkable
class SemanticIndexMetadataReader(Protocol):
    """Read the model identity associated with the current index."""

    def get_metadata(self) -> SemanticIndexMetadata | None: ...


@runtime_checkable
class SemanticIndexRebuilder(Protocol):
    """Atomically replace vectors and their embedding-model metadata."""

    def replace_all(
        self,
        documents: list[SemanticDocument],
        vectors: list[list[float]],
        metadata: SemanticIndexMetadata,
    ) -> None: ...


@runtime_checkable
class SemanticDocumentReader(Protocol):
    """Read all currently eligible entities as derived index documents."""

    def list_documents(self) -> list[SemanticDocument]: ...


@runtime_checkable
class SemanticEntityReader(Protocol):
    """Hydrate an eligible current entity after vector candidate retrieval."""

    def get_semantic_entity(self, kind: str, entity_id: str) -> SemanticEntity | None: ...
