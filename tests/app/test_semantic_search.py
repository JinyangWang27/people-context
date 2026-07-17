"""Semantic search validation, availability, mismatch, and merge tests."""

from __future__ import annotations

import pytest

from people_context.app.semantic_search import SemanticSearch, SemanticSearchValidationError
from people_context.ports.semantic import SemanticEntity, SemanticIndexMetadata, VectorSearchHit
from tests.app.fakes import (
    FakeEmbeddingProvider,
    FakeSemanticEntityReader,
    FakeSemanticMetadataReader,
    FakeVectorIndex,
)

_MODEL_ID = "test/model@revision"


def _search(
    metadata: SemanticIndexMetadata | None = None,
) -> tuple[SemanticSearch, FakeEmbeddingProvider, FakeVectorIndex, FakeSemanticEntityReader]:
    provider = FakeEmbeddingProvider(_MODEL_ID, 3)
    index = FakeVectorIndex()
    entities = FakeSemanticEntityReader()
    use_case = SemanticSearch(
        FakeSemanticMetadataReader(metadata),
        entities,
        lambda: provider,
        lambda: index,
        _MODEL_ID,
        3,
    )
    return use_case, provider, index, entities


def test_missing_metadata_is_not_available_without_embedding() -> None:
    use_case, provider, _, _ = _search()

    result = use_case.execute("SQL engineer")

    assert result.model_dump() == {
        "status": "not_available",
        "reason": "semantic index metadata is missing; run semantic reindex",
        "install": "uv sync --extra semantic",
        "retry": "uv run people-context reindex --semantic",
    }
    assert provider.calls == []


def test_model_mismatch_refuses_before_embedding() -> None:
    use_case, provider, _, _ = _search(SemanticIndexMetadata(model_id="old/model", dimension=256))

    result = use_case.execute("SQL engineer")

    assert result.model_dump() == {
        "status": "model_mismatch",
        "reason": "stored semantic vectors do not match the configured embedding model",
        "stored_model_id": "old/model",
        "current_model_id": _MODEL_ID,
        "retry": "uv run people-context reindex --semantic",
    }
    assert provider.calls == []


def test_search_merges_kinds_by_score_then_kind_then_entity_id() -> None:
    use_case, provider, index, entities = _search(SemanticIndexMetadata(model_id=_MODEL_ID, dimension=3))
    index.search_hits = {
        "person": [VectorSearchHit(entity_id="a", distance=0.1)],
        "interaction": [
            VectorSearchHit(entity_id="z", distance=0.1),
            VectorSearchHit(entity_id="y", distance=0.1),
        ],
    }
    entities.entities = {
        ("person", "a"): SemanticEntity(kind="person", entity_id="a", title="Alice", summary="SQL engineer"),
        ("interaction", "y"): SemanticEntity(kind="interaction", entity_id="y", title="Y", summary="SQL review"),
        ("interaction", "z"): SemanticEntity(kind="interaction", entity_id="z", title="Z", summary="SQL review"),
    }

    result = use_case.execute("SQL engineer", limit=3)

    assert result.status == "ok"
    assert provider.calls == [["SQL engineer"]]
    assert [(hit.kind, hit.entity_id) for hit in result.hits] == [
        ("interaction", "y"),
        ("interaction", "z"),
        ("person", "a"),
    ]
    assert [hit.score for hit in result.hits] == pytest.approx([0.9, 0.9, 0.9])


def test_search_fetches_deeper_candidates_when_stale_hits_would_underfill_limit() -> None:
    use_case, _, index, entities = _search(SemanticIndexMetadata(model_id=_MODEL_ID, dimension=3))
    index.search_hits = {
        "person": [
            VectorSearchHit(entity_id="stale-a", distance=0.01),
            VectorSearchHit(entity_id="stale-b", distance=0.02),
            VectorSearchHit(entity_id="person-a", distance=0.03),
            VectorSearchHit(entity_id="person-b", distance=0.04),
        ]
    }
    entities.entities = {
        ("person", "person-a"): SemanticEntity(
            kind="person", entity_id="person-a", title="Alice", summary="SQL engineer"
        ),
        ("person", "person-b"): SemanticEntity(
            kind="person", entity_id="person-b", title="Bob", summary="Database engineer"
        ),
    }

    result = use_case.execute("SQL engineer", kinds=["person"], limit=2)

    assert result.status == "ok"
    assert [hit.entity_id for hit in result.hits] == ["person-a", "person-b"]


@pytest.mark.parametrize(
    ("query", "kinds", "limit", "code"),
    [
        ("  ", None, 10, "invalid_query"),
        ("query", [], 10, "invalid_kinds"),
        ("query", ["fact"], 10, "invalid_kinds"),
        ("query", None, 0, "invalid_limit"),
        ("query", None, 101, "invalid_limit"),
    ],
)
def test_search_validation(query: str, kinds: list[str] | None, limit: int, code: str) -> None:
    use_case, _, _, _ = _search()

    with pytest.raises(SemanticSearchValidationError) as exc_info:
        use_case.execute(query, kinds=kinds, limit=limit)

    assert exc_info.value.code == code
