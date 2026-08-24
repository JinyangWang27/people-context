"""The decorators that keep derived vectors behind primary persistence.

These wrap every durable person and record write whenever semantic indexing is
active, so two properties matter more than any single delegation: the primary write
always reaches the underlying store, and a failing index never turns a successful
write into an error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.semantic_indexing import IndexingPeopleRepository, IndexingRecordStore
from people_context.app.semantic.indexing import SemanticIndexUpdater
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.person import Person
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind
from people_context.domain.shared import Provenance, Sensitivity, normalize_name
from people_context.domain.trait import Trait
from tests.app.fakes import (
    FakeEmbeddingProvider,
    FakePeopleRepository,
    FakeRecordStore,
    FakeVectorIndex,
)

_MODEL_ID = "test/model@revision"
_PROVENANCE = Provenance(source="test")
_DIMENSION = 3


class _ExplodingUpdater:
    """An index that fails every way it is asked to refresh."""

    def refresh_person(self, person: Person) -> None:
        raise RuntimeError("index unavailable")

    def refresh_interaction(self, interaction: Interaction) -> None:
        raise RuntimeError("index unavailable")

    def delete(self, entity_id: str) -> None:
        raise RuntimeError("index unavailable")


def _updater() -> tuple[SemanticIndexUpdater, FakeVectorIndex, FakeEmbeddingProvider]:
    index = FakeVectorIndex()
    provider = FakeEmbeddingProvider(_MODEL_ID, _DIMENSION)
    return SemanticIndexUpdater(provider, index), index, provider


def _interaction(summary: str = "Coffee", sensitivity: Sensitivity = Sensitivity.PERSONAL) -> Interaction:
    return Interaction(
        summary=summary,
        participant_ids=["p1"],
        occurred_at=datetime.now(UTC),
        sensitivity=sensitivity,
        provenance=_PROVENANCE,
    )


def test_person_writes_reach_the_store_and_refresh_the_vector() -> None:
    delegate = FakePeopleRepository()
    updater, index, provider = _updater()
    warnings: list[str] = []
    repository = IndexingPeopleRepository(delegate, updater, warnings.append)
    person = Person(canonical_name="Amina Hassan")

    repository.save_person(person)

    assert delegate.get(person.id) is not None
    assert index.vectors[person.id][0] == "person"
    assert warnings == []


def test_person_reads_are_delegated_unchanged() -> None:
    delegate = FakePeopleRepository()
    updater, _, _ = _updater()
    repository = IndexingPeopleRepository(delegate, updater, lambda _message: None)
    person = Person(canonical_name="Amina Hassan", is_self=True)
    repository.save_person(person)

    assert repository.get(person.id) == person
    assert repository.get_self() == person
    assert repository.list_people() == delegate.list_people()
    assert repository.find_by_normalized_name(normalize_name(person.canonical_name)) == [person]
    assert [hit.person for hit in repository.search_names("Amina")] == [person]
    assert repository.rebuild_person_search() == delegate.rebuild_person_search()


def test_a_failing_index_warns_without_failing_the_person_write() -> None:
    """The vector is derived data; losing it must never lose the primary write."""
    delegate = FakePeopleRepository()
    warnings: list[str] = []
    repository = IndexingPeopleRepository(delegate, _ExplodingUpdater(), warnings.append)
    person = Person(canonical_name="Amina Hassan")

    repository.save_person(person)

    assert delegate.get(person.id) is not None
    assert len(warnings) == 1
    assert "pctx reindex --semantic" in warnings[0]


def test_every_record_write_reaches_the_delegate() -> None:
    delegate = FakeRecordStore()
    updater, _, _ = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)
    now = datetime.now(UTC)

    relationship = Relationship(subject_id="a", object_id="b", type="friend_of", provenance=_PROVENANCE)
    affiliation = Affiliation(person_id="a", org_id="o", role="Engineer", provenance=_PROVENANCE)
    fact = Fact(person_id="a", predicate="birthday", value="--05-05", provenance=_PROVENANCE)
    observation = Observation(person_id="a", text="Prefers async updates", provenance=_PROVENANCE)
    trait = Trait(person_id="a", category="communication_style", value="direct", provenance=_PROVENANCE)
    reminder = Reminder(
        person_id="a", kind=ReminderKind.FOLLOW_UP, text="Follow up", due_at=now, provenance=_PROVENANCE
    )

    store.save_relationship(relationship)
    store.save_affiliation(affiliation)
    store.save_fact(fact)
    store.save_observation(observation)
    store.save_trait(trait)
    store.save_reminder(reminder)

    assert delegate.get_record("relationship", relationship.id) is relationship
    assert delegate.get_record("affiliation", affiliation.id) is affiliation
    assert delegate.get_record("fact", fact.id) is fact
    assert delegate.get_record("observation", observation.id) is observation
    assert delegate.get_record("trait", trait.id) is trait
    assert delegate.get_record("reminder", reminder.id) is reminder


def test_only_interactions_are_indexed_among_records() -> None:
    """Interactions carry the only record text the semantic index holds."""
    delegate = FakeRecordStore()
    updater, index, provider = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)

    store.save_fact(Fact(person_id="a", predicate="birthday", value="--05-05", provenance=_PROVENANCE))
    assert index.vectors == {}

    interaction = _interaction()
    store.save_interaction(interaction)
    assert index.vectors[interaction.id][0] == "interaction"


def test_record_reads_are_delegated_unchanged() -> None:
    delegate = FakeRecordStore()
    updater, _, _ = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)
    reminder = Reminder(
        person_id="a", kind=ReminderKind.FOLLOW_UP, text="Follow up", due_at=datetime.now(UTC), provenance=_PROVENANCE
    )
    store.save_reminder(reminder)

    assert store.get_record("reminder", reminder.id) is reminder
    assert store.list_reminders(person_id="a") == delegate.list_reminders(person_id="a")
    assert store.get_record("fact", "missing") is None


def test_updating_an_interaction_refreshes_its_vector() -> None:
    delegate = FakeRecordStore()
    updater, index, provider = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)
    interaction = _interaction("Coffee")
    store.save_interaction(interaction)

    updated = store.update_record_fields("interaction", interaction.id, {"summary": "Coffee and roadmap"})

    assert updated is not None
    assert index.vectors[interaction.id][0] == "interaction"
    assert provider.calls[-1] == ["Coffee and roadmap"]


def test_updating_a_non_interaction_record_leaves_the_index_alone() -> None:
    delegate = FakeRecordStore()
    updater, index, provider = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)
    fact = Fact(person_id="a", predicate="birthday", value="--05-05", provenance=_PROVENANCE)
    store.save_fact(fact)

    assert store.update_record_fields("fact", fact.id, {"value": "--06-06"}) is not None
    assert index.vectors == {}


def test_a_failing_index_warns_without_failing_the_interaction_write() -> None:
    delegate = FakeRecordStore()
    warnings: list[str] = []
    store = IndexingRecordStore(delegate, _ExplodingUpdater(), warnings.append)
    interaction = _interaction()

    store.save_interaction(interaction)

    assert delegate.get_record("interaction", interaction.id) is interaction
    assert len(warnings) == 1


@pytest.mark.parametrize("sensitivity", [Sensitivity.SENSITIVE, Sensitivity.RESTRICTED])
def test_a_non_ordinary_interaction_is_removed_from_the_index(sensitivity: Sensitivity) -> None:
    """Raising sensitivity must withdraw text the index should no longer hold."""
    delegate = FakeRecordStore()
    updater, index, provider = _updater()
    store = IndexingRecordStore(delegate, updater, lambda _message: None)
    ordinary = _interaction("Coffee")
    store.save_interaction(ordinary)
    assert ordinary.id in index.vectors

    store.save_interaction(_interaction("Coffee", sensitivity).model_copy(update={"id": ordinary.id}))

    assert ordinary.id not in index.vectors
