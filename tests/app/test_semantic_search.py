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


def test_search_merges_kinds_by_score_then_entity_id_and_skips_stale_entities() -> None:
    use_case, provider, index, entities = _search(SemanticIndexMetadata(model_id=_MODEL_ID, dimension=3))
    index.search_hits = {
        "person": [
            VectorSearchHit(entity_id="person-b", distance=0.1),
            VectorSearchHit(entity_id="stale", distance=0.05),
        ],
        "interaction": [VectorSearchHit(entity_id="interaction-a", distance=0.1)],
    }
    entities.entities = {
        ("person", "person-b"): SemanticEntity(
            kind="person", entity_id="person-b", title="Bob", summary="SQL engineer"
        ),
        ("interaction", "interaction-a"): SemanticEntity(
            kind="interaction", entity_id="interaction-a", title="Interaction", summary="SQL review"
        ),
    }

    result = use_case.execute("SQL engineer", limit=2)

    assert result.status == "ok"
    assert provider.calls == [["SQL engineer"]]
    assert [hit.entity_id for hit in result.hits] == ["interaction-a", "person-b"]
    assert [hit.score for hit in result.hits] == pytest.approx([0.9, 0.9])


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
