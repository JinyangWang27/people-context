"""sqlite-vec storage, document eligibility, and best-effort decorator tests."""

from __future__ import annotations

import json

import pytest

from people_context.adapters.semantic_indexing import IndexingPeopleRepository
from people_context.adapters.sqlite import SqlitePeopleRepository, open_db
from people_context.adapters.sqlite.semantic import (
    SEMANTIC_DIMENSION_KEY,
    SEMANTIC_MODEL_ID_KEY,
    SqliteSemanticDocumentReader,
    SqliteVectorIndex,
    create_sqlite_vector_index,
)
from people_context.app.semantic_indexing import SemanticIndexUpdater
from people_context.domain.person import Alias, Person
from people_context.ports.semantic import SemanticDocument, SemanticIndexMetadata, VectorSearchHit

sqlite_vec = pytest.importorskip("sqlite_vec")


def _vector(value: float) -> list[float]:
    return [value, *([0.0] * 255)]


class _Provider:
    model_id = "test/model@revision"
    dimension = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector(float(len(text))) for text in texts]


class _FailingIndex:
    def upsert(self, kind: str, entity_id: str, vector: list[float]) -> None:
        raise RuntimeError("vector disk full")

    def delete(self, entity_id: str) -> None:
        raise RuntimeError("vector disk full")

    def search(self, kind: str, vector: list[float], limit: int) -> list[VectorSearchHit]:
        return []


def test_vec0_schema_metadata_and_atomic_replacement() -> None:
    conn = open_db(":memory:")
    index = create_sqlite_vector_index(conn)
    old_document = SemanticDocument(kind="person", entity_id="old", text="Old")
    old_metadata = SemanticIndexMetadata(model_id="old/model", dimension=256)
    index.replace_all([old_document], [_vector(1.0)], old_metadata)

    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'semantic_vectors'"
    ).fetchone()[0]
    assert "entity_id TEXT PRIMARY KEY" in schema
    assert "kind TEXT PARTITION KEY" in schema
    assert "embedding FLOAT[256] DISTANCE_METRIC=cosine" in schema
    assert index.get_metadata() == old_metadata

    calls = 0

    def fail_on_second(vector: list[float]) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("serialization failed")
        return sqlite_vec.serialize_float32(vector)

    failing_index = SqliteVectorIndex(conn, fail_on_second)
    new_documents = [
        SemanticDocument(kind="person", entity_id="new-1", text="One"),
        SemanticDocument(kind="person", entity_id="new-2", text="Two"),
    ]
    with pytest.raises(RuntimeError, match="serialization failed"):
        failing_index.replace_all(
            new_documents,
            [_vector(2.0), _vector(3.0)],
            SemanticIndexMetadata(model_id="new/model", dimension=256),
        )

    assert [row[0] for row in conn.execute("SELECT entity_id FROM semantic_vectors")] == ["old"]
    assert index.get_metadata() == old_metadata


def test_document_reader_orders_person_fields_and_filters_interaction_sensitivity() -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)
    person = Person(
        id="person-1",
        canonical_name="Alice",
        aliases=[Alias(id="alias-2", value="A Li"), Alias(id="alias-1", value="Ally")],
        summary="SQL engineer",
    )
    repo.save_person(person)
    with conn:
        conn.executemany(
            """INSERT INTO interactions (
                   id, summary, occurred_at, sensitivity, provenance_source
               ) VALUES (?, ?, '2026-01-01T00:00:00+00:00', ?, 'test')""",
            [
                ("interaction-personal", "Public launch", "personal"),
                ("interaction-sensitive", "Private sentinel", "sensitive"),
            ],
        )

    documents = SqliteSemanticDocumentReader(conn).list_documents()

    assert documents == [
        SemanticDocument(kind="person", entity_id="person-1", text="Alice\nAlly\nA Li\nSQL engineer"),
        SemanticDocument(kind="interaction", entity_id="interaction-personal", text="Public launch"),
    ]
    assert "Private sentinel" not in str(documents)


def test_failed_incremental_vector_refresh_does_not_roll_back_person() -> None:
    conn = open_db(":memory:")
    primary = SqlitePeopleRepository(conn)
    warnings: list[str] = []
    decorated = IndexingPeopleRepository(
        primary,
        SemanticIndexUpdater(_Provider(), _FailingIndex()),
        warnings.append,
    )
    person = Person(canonical_name="Alice")

    decorated.save_person(person)

    assert primary.get(person.id) is not None
    assert len(warnings) == 1
    assert "run `uv run people-context reindex --semantic`" in warnings[0]


def test_metadata_is_stored_under_portable_preference_keys() -> None:
    conn = open_db(":memory:")
    index = create_sqlite_vector_index(conn)
    metadata = SemanticIndexMetadata(model_id="test/model@revision", dimension=256)
    index.replace_all([], [], metadata)

    stored = {
        row["key"]: json.loads(row["value_json"])
        for row in conn.execute(
            "SELECT key, value_json FROM user_preferences WHERE key IN (?, ?)",
            (SEMANTIC_MODEL_ID_KEY, SEMANTIC_DIMENSION_KEY),
        )
    }
    assert stored == {
        SEMANTIC_MODEL_ID_KEY: "test/model@revision",
        SEMANTIC_DIMENSION_KEY: 256,
    }


def test_cosine_search_scores_identical_as_one_and_orthogonal_as_zero() -> None:
    conn = open_db(":memory:")
    index = create_sqlite_vector_index(conn)
    query = _vector(1.0)
    orthogonal = [0.0, 1.0, *([0.0] * 254)]
    index.upsert("person", "identical", query)
    index.upsert("person", "orthogonal", orthogonal)

    hits = index.search("person", query, 2)
    scores = {hit.entity_id: 1.0 - hit.distance for hit in hits}

    assert scores["identical"] == pytest.approx(1.0)
    assert scores["orthogonal"] == pytest.approx(0.0)
