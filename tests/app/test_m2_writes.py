"""Unit tests for M2 write use cases against narrow in-memory ports."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from people_context.app import (
    AddAlias,
    AddAliasInput,
    CompleteReminder,
    CompleteReminderInput,
    CorrectRecord,
    CorrectRecordInput,
    InvalidCorrectionError,
    InvalidReminderError,
    PersonNotFoundError,
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
    RecordTrait,
    RecordTraitInput,
    ReminderNotActiveError,
    SetAffiliation,
    SetAffiliationInput,
    SetCommunicationPhilosophy,
    SetCommunicationPhilosophyInput,
    SetRelationship,
    SetRelationshipInput,
    SetReminder,
    SetReminderInput,
)
from people_context.domain.fact import Fact
from people_context.domain.person import AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.reminder import ReminderKind, ReminderStatus
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory
from tests.app.fakes import (
    FakeAuditLog,
    FakeClock,
    FakeOrganizationStore,
    FakePeopleRepository,
    FakePreferencesStore,
    FakeRecordStore,
)

_NOW = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def write_deps() -> tuple[FakePeopleRepository, FakeRecordStore, FakeAuditLog, FakeClock, Person, Person]:
    people = FakePeopleRepository()
    records = FakeRecordStore()
    audit = FakeAuditLog()
    clock = FakeClock(_NOW)
    self_person = Person(canonical_name="Me", is_self=True, created_at=_NOW, updated_at=_NOW)
    other = Person(canonical_name="Alice", created_at=_NOW, updated_at=_NOW)
    people.save_person(self_person)
    people.save_person(other)
    return people, records, audit, clock, self_person, other


def test_add_alias_deduplicates_normalized_values_and_audits(write_deps: tuple) -> None:
    people, _, audit, clock, _, other = write_deps
    use_case = AddAlias(people, people, audit, clock)

    use_case.execute(AddAliasInput(person_id=other.id, value="Áli", kind=AliasKind.NICKNAME))
    result = use_case.execute(AddAliasInput(person_id=other.id, value="Ali"))

    assert [alias.value for alias in result.aliases] == ["Áli"]
    assert [entry.payload["added"] for entry in audit.entries] == [True, False]


def test_all_person_targeted_writes_reject_unknown_person(write_deps: tuple) -> None:
    people, records, audit, clock, self_person, _ = write_deps
    organizations = FakeOrganizationStore()
    cases = [
        (AddAlias(people, people, audit, clock), AddAliasInput(person_id="missing", value="x")),
        (
            SetRelationship(people, records, audit, clock),
            SetRelationshipInput(subject_id=self_person.id, object_id="missing", type="friend_of"),
        ),
        (
            SetAffiliation(people, organizations, records, audit, clock),
            SetAffiliationInput(person_id="missing", org="Acme", role="Engineer"),
        ),
        (RecordFact(people, records, audit, clock), RecordFactInput(person_id="missing", predicate="p", value="v")),
        (
            RecordObservation(people, records, audit, clock),
            RecordObservationInput(person_id="missing", text="note"),
        ),
        (
            RecordTrait(people, records, audit, clock),
            RecordTraitInput(person_id="missing", category=TraitCategory.VALUES, value="kindness"),
        ),
        (
            SetReminder(people, records, audit, clock),
            SetReminderInput(
                person_id="missing", text="follow up", kind=ReminderKind.FOLLOW_UP, due_at=_NOW
            ),
        ),
    ]

    for use_case, data in cases:
        with pytest.raises(PersonNotFoundError):
            use_case.execute(data)
    assert audit.entries == []


def test_all_person_targeted_writes_reject_soft_deleted_person(write_deps: tuple) -> None:
    people, records, audit, clock, self_person, deleted = write_deps
    deleted.deleted_at = _NOW
    organizations = FakeOrganizationStore()
    cases = [
        (AddAlias(people, people, audit, clock), AddAliasInput(person_id=deleted.id, value="x")),
        (
            SetRelationship(people, records, audit, clock),
            SetRelationshipInput(subject_id=self_person.id, object_id=deleted.id, type="friend_of"),
        ),
        (
            SetAffiliation(people, organizations, records, audit, clock),
            SetAffiliationInput(person_id=deleted.id, org="Acme", role="Engineer"),
        ),
        (RecordFact(people, records, audit, clock), RecordFactInput(person_id=deleted.id, predicate="p", value="v")),
        (RecordObservation(people, records, audit, clock), RecordObservationInput(person_id=deleted.id, text="x")),
        (
            RecordTrait(people, records, audit, clock),
            RecordTraitInput(person_id=deleted.id, category=TraitCategory.VALUES, value="x"),
        ),
        (
            RecordInteraction(people, records, audit, clock),
            RecordInteractionInput(summary="x", participant_ids=[deleted.id]),
        ),
        (
            SetReminder(people, records, audit, clock),
            SetReminderInput(person_id=deleted.id, text="x", kind=ReminderKind.FOLLOW_UP, due_at=_NOW),
        ),
    ]

    for use_case, data in cases:
        with pytest.raises(PersonNotFoundError):
            use_case.execute(data)
    assert people.get(deleted.id) is deleted
    assert audit.entries == []


def test_relationship_affiliation_and_direct_records(write_deps: tuple) -> None:
    people, records, audit, clock, self_person, other = write_deps
    organizations = FakeOrganizationStore()
    relationship = SetRelationship(people, records, audit, clock).execute(
        SetRelationshipInput(
            subject_id=self_person.id,
            object_id=other.id,
            type="friend_of",
            valid_from=date(2024, 1, 1),
            source="agent:test",
            session="s1",
        )
    )
    affiliation = SetAffiliation(people, organizations, records, audit, clock).execute(
        SetAffiliationInput(person_id=other.id, org="Acme", role="Engineer")
    )
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=other.id, predicate="location", value="Dubai", sensitivity=Sensitivity.PUBLIC)
    )
    observation = RecordObservation(people, records, audit, clock).execute(
        RecordObservationInput(person_id=other.id, text="Seemed focused")
    )
    trait = RecordTrait(people, records, audit, clock).execute(
        RecordTraitInput(
            person_id=other.id,
            category=TraitCategory.COMMUNICATION_STYLE,
            value="Prefers concise notes",
        )
    )

    assert relationship.provenance.session == "s1"
    assert organizations.get(affiliation.org_id).name == "Acme"  # type: ignore[union-attr]
    assert records.get_record("fact", fact.id) == fact
    assert records.get_record("observation", observation.id) == observation
    assert records.get_record("trait", trait.id) == trait
    assert [entry.entity_type for entry in audit.entries] == [
        "relationship",
        "organization",
        "affiliation",
        "fact",
        "observation",
        "trait",
    ]


def test_interaction_requires_all_known_participants_and_deduplicates(write_deps: tuple) -> None:
    people, records, audit, clock, self_person, other = write_deps
    use_case = RecordInteraction(people, records, audit, clock)

    interaction = use_case.execute(
        RecordInteractionInput(
            summary="Discussed launch",
            participant_ids=[self_person.id, other.id, other.id],
            occurred_at=_NOW,
        )
    )

    assert interaction.participant_ids == [self_person.id, other.id]
    with pytest.raises(PersonNotFoundError):
        use_case.execute(RecordInteractionInput(summary="Unknown", participant_ids=["missing"]))
    assert len(audit.entries) == 1


def test_correct_fact_updates_in_place_and_rejects_identity_or_provenance(write_deps: tuple) -> None:
    people, records, audit, clock, _, other = write_deps
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=other.id, predicate="location", value="Dubia")
    )
    correct = CorrectRecord(records, records, audit, clock, people=people)

    updated = correct.execute(CorrectRecordInput(entity_type="fact", entity_id=fact.id, fields={"value": "Dubai"}))

    assert isinstance(updated, Fact)
    assert updated.id == fact.id
    assert updated.value == "Dubai"
    correction = audit.entries[-1]
    assert correction.op == "correct"
    assert correction.payload["before"]["value"] == "Dubia"
    assert correction.payload["after"]["value"] == "Dubai"
    for fields in ({"person_id": "other"}, {"provenance": {"source": "x"}}):
        with pytest.raises(InvalidCorrectionError):
            correct.execute(CorrectRecordInput(entity_type="fact", entity_id=fact.id, fields=fields))
    assert len(audit.entries) == 2


def test_reminder_transitions_and_kind_validation(write_deps: tuple) -> None:
    people, records, audit, clock, _, other = write_deps
    set_reminder = SetReminder(people, records, audit, clock)
    complete = CompleteReminder(records, records, audit, clock, people=people)
    reminder = set_reminder.execute(
        SetReminderInput(person_id=other.id, text="Follow up", kind=ReminderKind.FOLLOW_UP, due_at=_NOW)
    )

    completed = complete.execute(CompleteReminderInput(reminder_id=reminder.id))

    assert completed.status == ReminderStatus.COMPLETED
    assert audit.entries[-1].payload["fields"] == ["status"]
    with pytest.raises(ReminderNotActiveError):
        complete.execute(CompleteReminderInput(reminder_id=reminder.id))
    with pytest.raises(InvalidReminderError):
        set_reminder.execute(
            SetReminderInput(person_id=other.id, text="Note", kind=ReminderKind.COMMUNICATION_NOTE, due_at=_NOW)
        )


def test_record_correction_and_reminder_completion_reject_deleted_owner(write_deps: tuple) -> None:
    people, records, audit, clock, _, other = write_deps
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=other.id, predicate="location", value="Dubai")
    )
    reminder = SetReminder(people, records, audit, clock).execute(
        SetReminderInput(person_id=other.id, text="Follow up", kind=ReminderKind.FOLLOW_UP, due_at=_NOW)
    )
    other.deleted_at = _NOW

    with pytest.raises(PersonNotFoundError):
        CorrectRecord(records, records, audit, clock, people=people).execute(
            CorrectRecordInput(entity_type="fact", entity_id=fact.id, fields={"value": "Abu Dhabi"})
        )
    with pytest.raises(PersonNotFoundError):
        CompleteReminder(records, records, audit, clock, people=people).execute(
            CompleteReminderInput(reminder_id=reminder.id)
        )


def test_philosophy_round_trip_and_audit_contains_lengths_only(write_deps: tuple) -> None:
    _, _, audit, clock, _, _ = write_deps
    preferences = FakePreferencesStore()
    use_case = SetCommunicationPhilosophy(preferences, audit, clock)
    text = "道可道，非常道"

    result = use_case.execute(SetCommunicationPhilosophyInput(text=text))

    assert result.text == text
    assert preferences.get(PREF_COMMUNICATION_PHILOSOPHY) == text
    payload = audit.entries[0].payload
    assert payload == {"before_length": None, "after_length": len(text)}
    assert text not in str(payload)
