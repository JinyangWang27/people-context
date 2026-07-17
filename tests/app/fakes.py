"""In-memory fake implementations of the ports (proves Liskov substitutability).

These are plain Python objects backed by dicts/lists that structurally satisfy the
PersonReader / PersonWriter / AuditLog / Clock Protocols, so use cases can be exercised
without the SQLite adapter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.person import Person
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.domain.shared import normalize_name
from people_context.domain.trait import Trait
from people_context.ports.audit_log import AuditEntry
from people_context.ports.context import AffiliationRecord, RelationshipRecord
from people_context.ports.repository import SearchHit
from people_context.ports.semantic import (
    SemanticEntity,
    SemanticIndexMetadata,
    VectorSearchHit,
)


class FakeClock:
    """A clock returning a fixed, advanceable time."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FakeAuditLog:
    """An in-memory append-only audit log."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def append(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def list_entries(self, limit: int = 100) -> list[AuditEntry]:
        return list(reversed(self.entries))[:limit]


class FakePeopleRepository:
    """An in-memory PersonReader + PersonWriter backed by a dict.

    `search_names` does containment matching with a length-ratio score by default.
    Tests can force exact hits per query via `forced_hits` to control scores precisely.
    """

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}
        self.forced_hits: dict[str, list[SearchHit]] = {}

    # -- writer ------------------------------------------------------------

    def save_person(self, person: Person) -> None:
        self._people[person.id] = person

    # -- reader ------------------------------------------------------------

    def get(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def get_self(self) -> Person | None:
        for person in self._people.values():
            if person.is_self and person.deleted_at is None:
                return person
        return None

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]:
        people = [p for p in self._people.values() if include_deleted or p.deleted_at is None]
        people.sort(key=lambda p: p.canonical_name)
        return people[:limit] if limit is not None else people

    def find_by_normalized_name(self, normalized: str) -> list[Person]:
        matches: list[Person] = []
        for person in self._people.values():
            if person.deleted_at is not None:
                continue
            names = {normalize_name(name) for name in person.all_names()}
            if normalized in names:
                matches.append(person)
        return matches

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]:
        if query in self.forced_hits:
            return self.forced_hits[query][:limit]

        normalized_query = normalize_name(query)
        hits: list[SearchHit] = []
        for person in self._people.values():
            if person.deleted_at is not None:
                continue
            hit = self._best_containment(person, normalized_query)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda h: (-h.score, h.person.canonical_name))
        return hits[:limit]

    @staticmethod
    def _best_containment(person: Person, normalized_query: str) -> SearchHit | None:
        best: SearchHit | None = None
        candidates = [(person.canonical_name, "canonical"), *((a.value, "alias") for a in person.aliases)]
        for value, kind in candidates:
            normalized_value = normalize_name(value)
            if normalized_query and normalized_query in normalized_value:
                score = min(1.0, len(normalized_query) / len(normalized_value))
                if best is None or score > best.score:
                    best = SearchHit(person=person, score=score, matched_value=value, match_kind=kind)
        return best


class FakeContextReader:
    """In-memory context reader with the same active-record semantics as SQLite."""

    def __init__(self) -> None:
        self.relationships: list[RelationshipRecord] = []
        self.affiliations: list[AffiliationRecord] = []
        self.facts: list[Fact] = []
        self.observations: list[Observation] = []
        self.traits: list[Trait] = []
        self.interactions: list[Interaction] = []
        self.reminders: list[Reminder] = []

    def list_active_relationships(self, person_id: str, as_of: date) -> list[RelationshipRecord]:
        return [
            record
            for record in self.relationships
            if person_id in (record.relationship.subject_id, record.relationship.object_id)
            and (record.relationship.period.valid_to is None or record.relationship.period.valid_to >= as_of)
        ]

    def list_active_affiliations(self, person_id: str, as_of: date) -> list[AffiliationRecord]:
        return [
            record
            for record in self.affiliations
            if record.affiliation.person_id == person_id
            and (record.affiliation.period.valid_to is None or record.affiliation.period.valid_to >= as_of)
        ]

    def list_facts(self, person_id: str) -> list[Fact]:
        return [record for record in self.facts if record.person_id == person_id]

    def list_observations(self, person_id: str) -> list[Observation]:
        return [record for record in self.observations if record.person_id == person_id]

    def list_traits(self, person_id: str) -> list[Trait]:
        return [record for record in self.traits if record.person_id == person_id]

    def list_interactions(self, person_id: str) -> list[Interaction]:
        return [record for record in self.interactions if person_id in record.participant_ids]

    def list_active_reminders(self, person_id: str) -> list[Reminder]:
        return [
            record
            for record in self.reminders
            if record.person_id == person_id and record.status == ReminderStatus.ACTIVE
        ]


class FakeRecordStore:
    """In-memory RecordReader + RecordWriter for write-use-case tests."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], Any] = {}

    def save_relationship(self, relationship: Relationship) -> None:
        self.records[("relationship", relationship.id)] = relationship

    def save_affiliation(self, affiliation: Affiliation) -> None:
        self.records[("affiliation", affiliation.id)] = affiliation

    def save_fact(self, fact: Fact) -> None:
        self.records[("fact", fact.id)] = fact

    def save_observation(self, observation: Observation) -> None:
        self.records[("observation", observation.id)] = observation

    def save_trait(self, trait: Trait) -> None:
        self.records[("trait", trait.id)] = trait

    def save_interaction(self, interaction: Interaction) -> None:
        self.records[("interaction", interaction.id)] = interaction

    def save_reminder(self, reminder: Reminder) -> None:
        self.records[("reminder", reminder.id)] = reminder

    def get_record(self, entity_type: str, entity_id: str) -> Any | None:
        return self.records.get((entity_type, entity_id))

    def update_record_fields(self, entity_type: str, entity_id: str, fields: dict[str, Any]) -> Any | None:
        current = self.get_record(entity_type, entity_id)
        if current is None:
            return None
        data = current.model_dump()
        if "valid_from" in fields or "valid_to" in fields:
            period = current.period.model_dump()
            period.update({key: value for key, value in fields.items() if key in ("valid_from", "valid_to")})
            data["period"] = period
        data.update({key: value for key, value in fields.items() if key not in ("valid_from", "valid_to")})
        updated = type(current).model_validate(data)
        self.records[(entity_type, entity_id)] = updated
        return updated

    def list_reminders(
        self,
        person_id: str | None = None,
        due_before: datetime | None = None,
        status: ReminderStatus | None = ReminderStatus.ACTIVE,
    ) -> list[Reminder]:
        reminders = [record for (entity_type, _), record in self.records.items() if entity_type == "reminder"]
        reminders = [
            reminder
            for reminder in reminders
            if (person_id is None or reminder.person_id == person_id)
            and (
                due_before is None
                or (reminder.due_at is not None and reminder.due_at <= due_before)
                or (reminder.due_at is None and reminder.kind.value == "communication_note")
            )
            and (status is None or reminder.status == status)
        ]
        return sorted(
            reminders,
            key=lambda reminder: (
                reminder.due_at is None,
                reminder.due_at or datetime.max.replace(tzinfo=UTC),
                reminder.id,
            ),
        )


class FakeOrganizationStore:
    """In-memory organization store."""

    def __init__(self) -> None:
        self.organizations: dict[str, Organization] = {}

    def get(self, org_id: str) -> Organization | None:
        return self.organizations.get(org_id)

    def get_by_normalized_name(self, normalized_name: str) -> Organization | None:
        return next(
            (org for org in self.organizations.values() if normalize_name(org.name) == normalized_name),
            None,
        )

    def save(self, organization: Organization) -> None:
        self.organizations[organization.id] = organization


class FakePreferencesStore:
    """In-memory string preference store."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeEmbeddingProvider:
    """Deterministic embedding provider with call tracking."""

    def __init__(self, model_id: str = "test/model", dimension: int = 3) -> None:
        self.model_id = model_id
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(text)), *([0.0] * (self.dimension - 1))] for text in texts]


class FakeVectorIndex:
    """In-memory vector port with controlled search results."""

    def __init__(self) -> None:
        self.vectors: dict[str, tuple[str, list[float]]] = {}
        self.search_hits: dict[str, list[VectorSearchHit]] = {}

    def upsert(self, kind: str, entity_id: str, vector: list[float]) -> None:
        self.vectors[entity_id] = (kind, vector)

    def delete(self, entity_id: str) -> None:
        self.vectors.pop(entity_id, None)

    def search(self, kind: str, vector: list[float], limit: int) -> list[VectorSearchHit]:
        return self.search_hits.get(kind, [])[:limit]


class FakeSemanticMetadataReader:
    """Configurable semantic metadata port."""

    def __init__(self, metadata: SemanticIndexMetadata | None = None) -> None:
        self.metadata = metadata

    def get_metadata(self) -> SemanticIndexMetadata | None:
        return self.metadata


class FakeSemanticEntityReader:
    """In-memory safe semantic hydration port."""

    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], SemanticEntity] = {}

    def get_semantic_entity(self, kind: str, entity_id: str) -> SemanticEntity | None:
        return self.entities.get((kind, entity_id))
