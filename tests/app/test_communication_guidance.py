"""Tests for communication guidance and pull-based reminder listing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from people_context.app import (
    GetCommunicationGuidance,
    ListReminders,
    ListRemindersInput,
    PersonAffiliationContext,
    PersonRelationshipContext,
)
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.person import Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.context import AffiliationRecord, RelationshipRecord
from tests.app.fakes import FakeClock, FakeContextReader, FakePeopleRepository, FakePreferencesStore, FakeRecordStore

_NOW = datetime(2025, 6, 10, 10, 0, tzinfo=UTC)
_PROVENANCE = Provenance(source="test")


def test_guidance_returns_exact_signal_bundle_with_privacy_gate_and_cap() -> None:
    people = FakePeopleRepository()
    context = FakeContextReader()
    preferences = FakePreferencesStore()
    clock = FakeClock(_NOW)
    person = Person(canonical_name="Alice", created_at=_NOW, updated_at=_NOW)
    other = Person(canonical_name="Me", is_self=True, created_at=_NOW, updated_at=_NOW)
    people.save_person(person)
    people.save_person(other)
    relationship = Relationship(
        subject_id=other.id, object_id=person.id, type="friend_of", provenance=_PROVENANCE, created_at=_NOW
    )
    affiliation = Affiliation(
        person_id=person.id, org_id="org-1", role="Engineer", provenance=_PROVENANCE, created_at=_NOW
    )
    context.relationships.append(
        RelationshipRecord(relationship=relationship, other_person_id=other.id, other_person_name="Me")
    )
    context.affiliations.append(AffiliationRecord(affiliation=affiliation, organization_name="Acme"))
    context.traits.extend(
        [
            Trait(
                person_id=person.id,
                category=TraitCategory.COMMUNICATION_STYLE,
                value="Prefers concise writing",
                provenance=_PROVENANCE,
            ),
            Trait(
                person_id=person.id,
                category=TraitCategory.TOPICS_TO_AVOID,
                value="Private topic",
                sensitivity=Sensitivity.RESTRICTED,
                provenance=_PROVENANCE,
            ),
        ]
    )
    context.interactions.extend(
        [
            Interaction(
                summary=f"Note {index}",
                occurred_at=_NOW - timedelta(days=index),
                participant_ids=[person.id],
                provenance=_PROVENANCE,
            )
            for index in range(6)
        ]
    )
    context.interactions.append(
        Interaction(
            summary="Secret",
            occurred_at=_NOW + timedelta(days=1),
            participant_ids=[person.id],
            sensitivity=Sensitivity.SENSITIVE,
            provenance=_PROVENANCE,
        )
    )
    context.observations.append(Observation(person_id=person.id, text="Do not expose", provenance=_PROVENANCE))
    note = Reminder(person_id=person.id, text="Use email", kind=ReminderKind.COMMUNICATION_NOTE)
    context.reminders.extend(
        [note, Reminder(person_id=person.id, text="Call", kind=ReminderKind.FOLLOW_UP, due_at=_NOW)]
    )
    philosophy = "上善若水"
    preferences.set(PREF_COMMUNICATION_PHILOSOPHY, philosophy)

    result = GetCommunicationGuidance(people, context, preferences, clock).execute(
        person.id, situation="Discuss scope"
    )

    assert result.found is True
    assert result.situation == "Discuss scope"
    assert list(result.traits) == [TraitCategory.COMMUNICATION_STYLE.value]
    assert isinstance(result.relationships[0], PersonRelationshipContext)
    assert isinstance(result.affiliations[0], PersonAffiliationContext)
    assert result.friction_notes == [f"Note {index}" for index in range(5)]
    assert result.reminders == [note]
    assert result.communication_philosophy == philosophy
    assert result.philosophy_set is True
    assert "Secret" not in result.model_dump_json()
    assert "Do not expose" not in result.model_dump_json()


def test_guidance_not_found_and_unset_philosophy_have_stable_shape() -> None:
    result = GetCommunicationGuidance(
        FakePeopleRepository(), FakeContextReader(), FakePreferencesStore(), FakeClock(_NOW)
    ).execute("missing", situation="Hello")

    assert result.model_dump(mode="json") == {
        "found": False,
        "person_id": "missing",
        "situation": "Hello",
        "traits": {},
        "relationships": [],
        "affiliations": [],
        "friction_notes": [],
        "reminders": [],
        "communication_philosophy": None,
        "philosophy_set": False,
    }


def test_list_reminders_filters_due_items_and_keeps_communication_notes_last() -> None:
    records = FakeRecordStore()
    due_soon = Reminder(
        person_id="p1", text="Soon", kind=ReminderKind.FOLLOW_UP, due_at=_NOW + timedelta(days=1)
    )
    due_later = Reminder(
        person_id="p1", text="Later", kind=ReminderKind.FOLLOW_UP, due_at=_NOW + timedelta(days=5)
    )
    note = Reminder(person_id="p1", text="Be kind", kind=ReminderKind.COMMUNICATION_NOTE)
    completed = Reminder(
        person_id="p1", text="Done", kind=ReminderKind.FOLLOW_UP, due_at=_NOW, status=ReminderStatus.COMPLETED
    )
    for reminder in (note, due_later, completed, due_soon):
        records.save_reminder(reminder)

    result = ListReminders(records).execute(
        ListRemindersInput(person_id="p1", due_before=_NOW + timedelta(days=2))
    )

    assert [reminder.id for reminder in result] == [due_soon.id, note.id]
