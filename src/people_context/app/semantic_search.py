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
            candidates = [
                (kind, hit.entity_id, 1.0 - hit.distance)
                for kind in selected_kinds
                for hit in index.search(kind, vector, limit)
            ]
        except Exception as exc:  # noqa: BLE001 - optional package/model/index failures are result states
            return SemanticSearchNotAvailable(reason=str(exc) or exc.__class__.__name__)
        candidates.sort(key=lambda item: (-item[2], item[1], item[0]))
        hits: list[SemanticSearchHit] = []
        for kind, entity_id, score in candidates:
            entity = self._entities.get_semantic_entity(kind, entity_id)
            if entity is None:
                continue
            hits.append(
                SemanticSearchHit(
                    kind=kind,
                    entity_id=entity_id,
                    score=score,
                    title=entity.title,
                    summary=entity.summary,
                )
            )
            if len(hits) == limit:
                break
        return SemanticSearchOk(model_id=self._current_model_id, hits=hits)

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
