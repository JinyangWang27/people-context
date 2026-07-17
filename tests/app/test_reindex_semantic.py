"""Semantic rebuild and incremental-update application tests."""

from __future__ import annotations

from people_context.app.reindex_semantic import ReindexSemantic
from people_context.app.semantic_indexing import SemanticIndexUpdater
from people_context.domain.interaction import Interaction
from people_context.domain.person import Alias, Person
from people_context.domain.shared import Provenance, Sensitivity
from people_context.ports.semantic import SemanticDocument, SemanticIndexMetadata, VectorSearchHit


class _Provider:
    model_id = "test/model@revision"
    dimension = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]


class _Documents:
    def list_documents(self) -> list[SemanticDocument]:
        return [
            SemanticDocument(kind="person", entity_id="person-1", text="Alice\nEngineer"),
            SemanticDocument(kind="interaction", entity_id="interaction-1", text="Discussed SQL"),
        ]


class _Index:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, str, list[float]]] = []
        self.deleted: list[str] = []
        self.rebuild: tuple[list[SemanticDocument], list[list[float]], SemanticIndexMetadata] | None = None

    def upsert(self, kind: str, entity_id: str, vector: list[float]) -> None:
        self.upserts.append((kind, entity_id, vector))

    def delete(self, entity_id: str) -> None:
        self.deleted.append(entity_id)

    def search(self, kind: str, vector: list[float], limit: int) -> list[VectorSearchHit]:
        return []

    def replace_all(
        self,
        documents: list[SemanticDocument],
        vectors: list[list[float]],
        metadata: SemanticIndexMetadata,
    ) -> None:
        self.rebuild = (documents, vectors, metadata)


def test_reindex_semantic_embeds_all_documents_and_replaces_once() -> None:
    index = _Index()

    result = ReindexSemantic(_Documents(), _Provider(), index).execute()

    assert result.model_dump() == {
        "entities": 2,
        "people": 1,
        "interactions": 1,
        "model_id": "test/model@revision",
        "dimension": 3,
    }
    assert index.rebuild is not None
    documents, vectors, metadata = index.rebuild
    assert [document.entity_id for document in documents] == ["person-1", "interaction-1"]
    assert vectors == [[14.0, 1.0, 0.0], [13.0, 1.0, 0.0]]
    assert metadata == SemanticIndexMetadata(model_id="test/model@revision", dimension=3)


def test_incremental_updater_builds_person_document_and_removes_private_interactions() -> None:
    index = _Index()
    updater = SemanticIndexUpdater(_Provider(), index)
    person = Person(canonical_name="Alice", aliases=[Alias(value="Ally")], summary="SQL engineer")

    updater.refresh_person(person)
    updater.refresh_interaction(
        Interaction(
            id="private-interaction",
            summary="Secret",
            sensitivity=Sensitivity.SENSITIVE,
            provenance=Provenance(source="test"),
        )
    )

    assert index.upserts == [("person", person.id, [23.0, 1.0, 0.0])]
    assert index.deleted == ["private-interaction"]
