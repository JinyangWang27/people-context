"""Integration tests for M2 SQLite record, organization, and preference stores."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from people_context.adapters.sqlite import (
    SqliteOrganizationStore,
    SqlitePreferencesStore,
    SqliteRecordStore,
    open_db,
)
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod, normalize_name
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.records import OrganizationStore, PreferencesStore, RecordReader, RecordWriter

_NOW = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
_PROVENANCE = Provenance(source="agent", session="s1", stated_by="self")


def _seed_people(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            """INSERT INTO persons
               (id, canonical_name, canonical_name_normalized, is_self, created_at, updated_at)
               VALUES ('p1', 'Me', 'me', 1, ?, ?), ('p2', 'Alice', 'alice', 0, ?, ?)""",
            (_NOW.isoformat(), _NOW.isoformat(), _NOW.isoformat(), _NOW.isoformat()),
        )


def test_all_record_types_round_trip_and_field_update() -> None:
    conn = open_db(":memory:")
    _seed_people(conn)
    store = SqliteRecordStore(conn)
    orgs = SqliteOrganizationStore(conn)
    organization = Organization(name="道可道 Labs")
    orgs.save(organization)

    records = [
        Relationship(
            subject_id="p1", object_id="p2", type="friend_of", provenance=_PROVENANCE, created_at=_NOW
        ),
        Affiliation(person_id="p2", org_id=organization.id, role="Engineer", provenance=_PROVENANCE, created_at=_NOW),
        Fact(
            person_id="p2", predicate="location", value="Dubai", provenance=_PROVENANCE, recorded_at=_NOW
        ),
        Observation(person_id="p2", text="Calm", provenance=_PROVENANCE, observed_at=_NOW),
        Trait(
            person_id="p2",
            category=TraitCategory.COMMUNICATION_STYLE,
            value="Prefers writing",
            provenance=_PROVENANCE,
            updated_at=_NOW,
        ),
        Interaction(
            summary="Discussed launch",
            participant_ids=["p1", "p2", "p2"],
            provenance=_PROVENANCE,
            occurred_at=_NOW,
        ),
        Reminder(person_id="p2", text="Follow up", kind=ReminderKind.FOLLOW_UP, due_at=_NOW, created_at=_NOW),
    ]
    save_methods = [
        store.save_relationship,
        store.save_affiliation,
        store.save_fact,
        store.save_observation,
        store.save_trait,
        store.save_interaction,
        store.save_reminder,
    ]

    for record, save in zip(records, save_methods, strict=True):
        save(record)  # type: ignore[arg-type]

    entity_types = ["relationship", "affiliation", "fact", "observation", "trait", "interaction", "reminder"]
    loaded = [
        store.get_record(entity_type, record.id)
        for entity_type, record in zip(entity_types, records, strict=True)
    ]

    assert all(record is not None for record in loaded)
    interaction = loaded[5]
    assert isinstance(interaction, Interaction)
    assert interaction.participant_ids == ["p1", "p2"]
    updated = store.update_record_fields("fact", records[2].id, {"value": "Abu Dhabi"})
    assert isinstance(updated, Fact)
    assert updated.value == "Abu Dhabi"


def test_organization_preferences_and_reminder_filters() -> None:
    conn = open_db(":memory:")
    _seed_people(conn)
    store = SqliteRecordStore(conn)
    orgs = SqliteOrganizationStore(conn)
    preferences = SqlitePreferencesStore(conn)
    organization = Organization(name="Café Team")
    orgs.save(organization)
    preferences.set("communication_philosophy", "道可道，非常道")
    due = Reminder(
        person_id="p2",
        text="Call",
        kind=ReminderKind.FOLLOW_UP,
        due_at=datetime(2025, 6, 2, tzinfo=UTC),
        created_at=_NOW,
    )
    note = Reminder(person_id="p2", text="Be concise", kind=ReminderKind.COMMUNICATION_NOTE, created_at=_NOW)
    completed = Reminder(
        person_id="p2",
        text="Done",
        kind=ReminderKind.FOLLOW_UP,
        status=ReminderStatus.COMPLETED,
        created_at=_NOW,
    )
    for reminder in (note, completed, due):
        store.save_reminder(reminder)

    assert orgs.get(organization.id) == organization
    assert orgs.get_by_normalized_name(normalize_name("Cafe Team")) == organization
    assert preferences.get("communication_philosophy") == "道可道，非常道"
    assert [item.id for item in store.list_reminders(person_id="p2")] == [due.id, note.id]
    assert [item.id for item in store.list_reminders(due_before=datetime(2025, 6, 1, tzinfo=UTC))] == [note.id]

    writer: RecordWriter = store
    reader: RecordReader = store
    organization_port: OrganizationStore = orgs
    preferences_port: PreferencesStore = preferences
    assert isinstance(writer, RecordWriter)
    assert isinstance(reader, RecordReader)
    assert isinstance(organization_port, OrganizationStore)
    assert isinstance(preferences_port, PreferencesStore)


def test_update_serializes_dates_and_enums() -> None:
    conn = open_db(":memory:")
    _seed_people(conn)
    store = SqliteRecordStore(conn)
    fact = Fact(person_id="p2", predicate="role", value="Engineer", provenance=_PROVENANCE, recorded_at=_NOW)
    store.save_fact(fact)

    updated = store.update_record_fields(
        "fact",
        fact.id,
        {"valid_from": date(2025, 1, 1), "sensitivity": Sensitivity.SENSITIVE},
    )

    assert isinstance(updated, Fact)
    assert updated.period == ValidityPeriod(valid_from=date(2025, 1, 1))
    assert updated.sensitivity == Sensitivity.SENSITIVE
