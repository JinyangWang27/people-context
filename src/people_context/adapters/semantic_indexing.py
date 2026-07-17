"""Best-effort decorators that keep semantic vectors behind primary persistence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from people_context.app.semantic_indexing import SemanticIndexUpdater
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.person import Person
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.domain.trait import Trait
from people_context.ports.audit_log import AuditEntry
from people_context.ports.lifecycle import LifecycleStore
from people_context.ports.records import Record, RecordReader, RecordWriter
from people_context.ports.repository import PersonReader, PersonSearchIndexer, PersonWriter, SearchHit

_WARNING_SUFFIX = "Primary data was saved; run `uv run people-context reindex --semantic` to repair vectors."


class IndexingPeopleRepository:
    """Delegate person persistence, then refresh the derived vector best-effort."""

    def __init__(
        self,
        delegate: PersonReader | PersonWriter | PersonSearchIndexer,
        updater: SemanticIndexUpdater,
        warn: Callable[[str], None],
    ) -> None:
        self._delegate = delegate
        self._updater = updater
        self._warn = warn

    def save_person(self, person: Person) -> None:
        assert isinstance(self._delegate, PersonWriter)
        self._delegate.save_person(person)
        self._best_effort(lambda: self._updater.refresh_person(person))

    def get(self, person_id: str) -> Person | None:
        assert isinstance(self._delegate, PersonReader)
        return self._delegate.get(person_id)

    def get_self(self) -> Person | None:
        assert isinstance(self._delegate, PersonReader)
        return self._delegate.get_self()

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]:
        assert isinstance(self._delegate, PersonReader)
        return self._delegate.list_people(include_deleted=include_deleted, limit=limit)

    def find_by_normalized_name(self, normalized: str) -> list[Person]:
        assert isinstance(self._delegate, PersonReader)
        return self._delegate.find_by_normalized_name(normalized)

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]:
        assert isinstance(self._delegate, PersonReader)
        return self._delegate.search_names(query, limit=limit)

    def rebuild_person_search(self) -> tuple[int, int]:
        assert isinstance(self._delegate, PersonSearchIndexer)
        return self._delegate.rebuild_person_search()

    def _best_effort(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - derived index must never fail the primary write
            self._warn(f"Semantic index refresh failed: {exc}. {_WARNING_SUFFIX}")


class IndexingRecordStore:
    """Delegate record persistence, refreshing only semantic interaction documents."""

    def __init__(
        self,
        delegate: RecordReader | RecordWriter,
        updater: SemanticIndexUpdater,
        warn: Callable[[str], None],
    ) -> None:
        self._delegate = delegate
        self._updater = updater
        self._warn = warn

    def save_relationship(self, relationship: Relationship) -> None:
        self._writer.save_relationship(relationship)

    def save_affiliation(self, affiliation: Affiliation) -> None:
        self._writer.save_affiliation(affiliation)

    def save_fact(self, fact: Fact) -> None:
        self._writer.save_fact(fact)

    def save_observation(self, observation: Observation) -> None:
        self._writer.save_observation(observation)

    def save_trait(self, trait: Trait) -> None:
        self._writer.save_trait(trait)

    def save_interaction(self, interaction: Interaction) -> None:
        self._writer.save_interaction(interaction)
        self._best_effort(lambda: self._updater.refresh_interaction(interaction))

    def save_reminder(self, reminder: Reminder) -> None:
        self._writer.save_reminder(reminder)

    def get_record(self, entity_type: str, entity_id: str) -> Record | None:
        return self._reader.get_record(entity_type, entity_id)

    def update_record_fields(self, entity_type: str, entity_id: str, fields: dict[str, Any]) -> Record | None:
        record = self._writer.update_record_fields(entity_type, entity_id, fields)
        if isinstance(record, Interaction):
            self._best_effort(lambda: self._updater.refresh_interaction(record))
        return record

    def list_reminders(
        self,
        person_id: str | None = None,
        due_before: datetime | None = None,
        status: ReminderStatus | None = ReminderStatus.ACTIVE,
    ) -> list[Reminder]:
        return self._reader.list_reminders(person_id=person_id, due_before=due_before, status=status)

    @property
    def _writer(self) -> RecordWriter:
        assert isinstance(self._delegate, RecordWriter)
        return self._delegate

    @property
    def _reader(self) -> RecordReader:
        assert isinstance(self._delegate, RecordReader)
        return self._delegate

    def _best_effort(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - derived index must never fail the primary write
            self._warn(f"Semantic index refresh failed: {exc}. {_WARNING_SUFFIX}")


class IndexingLifecycleStore:
    """Delegate atomic lifecycle mutations, then repair affected entity vectors."""

    def __init__(
        self,
        delegate: LifecycleStore,
        updater: SemanticIndexUpdater,
        warn: Callable[[str], None],
    ) -> None:
        self._delegate = delegate
        self._updater = updater
        self._warn = warn

    def merge_people(
        self,
        primary: Person,
        duplicate_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        counts = self._delegate.merge_people(primary, duplicate_id, audit_factory)
        self._best_effort(lambda: self._updater.refresh_person(primary))
        self._best_effort(lambda: self._updater.delete(duplicate_id))
        return counts

    def forget_person(
        self,
        person_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        counts = self._delegate.forget_person(person_id, audit_factory)
        self._best_effort(lambda: self._updater.delete(person_id))
        return counts

    def forget_record(
        self,
        entity_type: str,
        entity_id: str,
        audit_factory: Callable[[dict[str, int]], AuditEntry],
    ) -> dict[str, int]:
        counts = self._delegate.forget_record(entity_type, entity_id, audit_factory)
        self._best_effort(lambda: self._updater.delete(entity_id))
        return counts

    def preview_person_forget(self, person_id: str) -> dict[str, int]:
        return self._delegate.preview_person_forget(person_id)

    def _best_effort(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - derived index must never fail the primary write
            self._warn(f"Semantic index refresh failed: {exc}. {_WARNING_SUFFIX}")


def create_local_semantic_updater(conn: Any) -> SemanticIndexUpdater | None:
    """Create an updater only for an existing, matching, locally usable semantic index."""
    from people_context.adapters.model2vec_embeddings import (
        MODEL_DIMENSION,
        MODEL_ID,
        create_local_embedding_provider,
    )
    from people_context.adapters.sqlite.semantic import open_sqlite_vector_index, read_semantic_metadata

    metadata = read_semantic_metadata(conn)
    if metadata is None:
        return None
    if metadata.model_id != MODEL_ID or metadata.dimension != MODEL_DIMENSION:
        raise ValueError("stored semantic model metadata does not match the configured model")
    return SemanticIndexUpdater(create_local_embedding_provider(), open_sqlite_vector_index(conn))
