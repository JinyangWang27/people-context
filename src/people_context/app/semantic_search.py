"""Validated, model-safe semantic search across people and interactions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from people_context.ports.semantic import (
    EmbeddingProvider,
    SemanticEntityReader,
    SemanticIndexMetadataReader,
    VectorIndex,
)

_SUPPORTED_KINDS = frozenset({"person", "interaction"})
_INSTALL = "uv sync --extra semantic"
_RETRY = "uv run people-context reindex --semantic"


class SemanticSearchValidationError(ValueError):
    """Raised for invalid query, kinds, or limit inputs."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SemanticSearchHit(BaseModel):
    """One hydrated semantic match."""

    kind: str
    entity_id: str
    score: float
    title: str
    summary: str


class SemanticSearchOk(BaseModel):
    """Successful semantic-search response."""

    status: Literal["ok"] = "ok"
    model_id: str
    hits: list[SemanticSearchHit] = Field(default_factory=list)


class SemanticSearchNotAvailable(BaseModel):
    """Actionable response when semantic search cannot run locally."""

    status: Literal["not_available"] = "not_available"
    reason: str
    install: str = _INSTALL
    retry: str = _RETRY


class SemanticSearchModelMismatch(BaseModel):
    """Actionable refusal when stored vectors use another model identity."""

    status: Literal["model_mismatch"] = "model_mismatch"
    reason: str
    stored_model_id: str
    current_model_id: str
    retry: str = _RETRY


SemanticSearchResult = SemanticSearchOk | SemanticSearchNotAvailable | SemanticSearchModelMismatch


class SemanticSearch:
    """Check model metadata, embed locally, query per kind, and safely hydrate hits."""

    def __init__(
        self,
        metadata: SemanticIndexMetadataReader,
        entities: SemanticEntityReader,
        provider_factory: Callable[[], EmbeddingProvider],
        index_factory: Callable[[], VectorIndex],
        current_model_id: str,
        current_dimension: int,
    ) -> None:
        self._metadata = metadata
        self._entities = entities
        self._provider_factory = provider_factory
        self._index_factory = index_factory
        self._current_model_id = current_model_id
        self._current_dimension = current_dimension

    def execute(
        self,
        query: str,
        kinds: list[str] | None = None,
        limit: int = 10,
    ) -> SemanticSearchResult:
        selected_kinds = kinds if kinds is not None else ["person", "interaction"]
        self._validate(query, selected_kinds, limit)
        metadata = self._metadata.get_metadata()
        if metadata is None:
            return SemanticSearchNotAvailable(reason="semantic index metadata is missing; run semantic reindex")
        if metadata.model_id != self._current_model_id or metadata.dimension != self._current_dimension:
            return SemanticSearchModelMismatch(
                reason="stored semantic vectors do not match the configured embedding model",
                stored_model_id=metadata.model_id,
                current_model_id=self._current_model_id,
            )
        try:
            index = self._index_factory()
            provider = self._provider_factory()
            vector = provider.embed([query.strip()])[0]
            if len(vector) != self._current_dimension:
                raise ValueError("embedding provider returned an unexpected vector dimension")
            hits = [
                hit
                for kind in selected_kinds
                for hit in self._search_kind(index, vector, kind, limit)
            ]
        except Exception as exc:  # noqa: BLE001 - optional package/model/index failures are result states
            return SemanticSearchNotAvailable(reason=str(exc) or exc.__class__.__name__)
        hits.sort(key=lambda hit: (-hit.score, hit.kind, hit.entity_id))
        return SemanticSearchOk(model_id=self._current_model_id, hits=hits[:limit])

    def _search_kind(
        self,
        index: VectorIndex,
        vector: list[float],
        kind: str,
        limit: int,
    ) -> list[SemanticSearchHit]:
        search_limit = limit
        hydrated: dict[str, SemanticSearchHit | None] = {}
        while True:
            vector_hits = index.search(kind, vector, search_limit)
            candidates: list[SemanticSearchHit] = []
            seen_entity_ids: set[str] = set()
            for vector_hit in vector_hits:
                if vector_hit.entity_id in seen_entity_ids:
                    continue
                seen_entity_ids.add(vector_hit.entity_id)
                if vector_hit.entity_id not in hydrated:
                    entity = self._entities.get_semantic_entity(kind, vector_hit.entity_id)
                    hydrated[vector_hit.entity_id] = (
                        None
                        if entity is None
                        else SemanticSearchHit(
                            kind=kind,
                            entity_id=vector_hit.entity_id,
                            score=1.0 - vector_hit.distance,
                            title=entity.title,
                            summary=entity.summary,
                        )
                    )
                if hit := hydrated[vector_hit.entity_id]:
                    candidates.append(hit)
            candidates.sort(key=lambda hit: (-hit.score, hit.entity_id))
            if len(candidates) >= limit or len(vector_hits) < search_limit:
                return candidates[:limit]
            search_limit *= 2

    @staticmethod
    def _validate(query: str, kinds: list[str], limit: int) -> None:
        if not query.strip():
            raise SemanticSearchValidationError("invalid_query", "query must not be blank")
        if not kinds:
            raise SemanticSearchValidationError("invalid_kinds", "kinds must not be empty")
        unsupported = sorted(set(kinds) - _SUPPORTED_KINDS)
        if unsupported:
            raise SemanticSearchValidationError(
                "invalid_kinds",
                f"unsupported semantic kinds: {', '.join(unsupported)}",
            )
        if not 1 <= limit <= 100:
            raise SemanticSearchValidationError("invalid_limit", "limit must be between 1 and 100")
