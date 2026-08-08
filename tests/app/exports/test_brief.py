"""Application policy for the deterministic person brief (M14.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from people_context.app.context import GetCommunicationGuidance, GetPersonContext
from people_context.app.exports import (
    BRIEF_FORMAT,
    BRIEF_VERSION,
    ComposePersonBrief,
    DisclosureLevel,
    PersonBriefDocument,
    render_brief_json,
    render_brief_markdown,
)
from people_context.app.records import ListReminders
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.organization import Affiliation
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.context import AffiliationRecord, RelationshipRecord
from tests.app.fakes import (
    FakeClock,
    FakeContextReader,
    FakePeopleRepository,
    FakePreferencesStore,
    FakeRecordStore,
)

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
_PROVENANCE = Provenance(source="test")


class _Harness:
    """One wired `ComposePersonBrief` over in-memory ports."""

    def __init__(self) -> None:
        self.people = FakePeopleRepository()
        self.context = FakeContextReader()
        self.records = FakeRecordStore()
        self.preferences = FakePreferencesStore()
        self.clock = FakeClock(_NOW)
        self.brief = ComposePersonBrief(
            GetPersonContext(self.people, self.context, self.clock),
            GetCommunicationGuidance(self.people, self.context, self.preferences, self.clock),
            ListReminders(self.records),
            self.clock,
        )

    def add_person(self, name: str = "Alice Zhang", **fields: object) -> Person:
        person = Person(canonical_name=name, created_at=_NOW, updated_at=_NOW, **fields)
        self.people.save_person(person)
        return person

    def add_reminder(self, reminder: Reminder) -> Reminder:
        self.records.save_reminder(reminder)
        self.context.reminders.append(reminder)
        return reminder


def _reminder(person_id: str, text: str, kind: ReminderKind, due_at: datetime | None = None) -> Reminder:
    return Reminder(person_id=person_id, text=text, kind=kind, due_at=due_at, created_at=_NOW)


def test_missing_or_deleted_person_yields_no_document() -> None:
    harness = _Harness()
    deleted = harness.add_person("Gone", deleted_at=_NOW)

    assert harness.brief.execute("nobody") is None
    assert harness.brief.execute(deleted.id) is None


def test_brief_carries_every_active_reminder_kind() -> None:
    harness = _Harness()
    person = harness.add_person()
    follow_up = harness.add_reminder(
        _reminder(person.id, "Send the notes", ReminderKind.FOLLOW_UP, _NOW + timedelta(days=2))
    )
    occasion = harness.add_reminder(
        _reminder(person.id, "Team offsite", ReminderKind.OCCASION, _NOW + timedelta(days=1))
    )
    note = harness.add_reminder(_reminder(person.id, "Prefers short messages", ReminderKind.COMMUNICATION_NOTE))
    completed = _reminder(person.id, "Already done", ReminderKind.FOLLOW_UP, _NOW)
    harness.add_reminder(completed.model_copy(update={"status": ReminderStatus.COMPLETED}))

    document = harness.brief.execute(person.id)

    assert document is not None
    # Neither `GetPersonContext` nor `GetCommunicationGuidance` returns follow_up or occasion
    # rows, so their presence here proves the brief really composes `ListReminders`.
    assert [reminder.id for reminder in document.reminders] == [occasion.id, follow_up.id, note.id]
    assert {reminder.kind for reminder in document.reminders} == set(ReminderKind)
    assert all(reminder.status == ReminderStatus.ACTIVE for reminder in document.reminders)


def test_reminders_of_another_person_are_excluded() -> None:
    harness = _Harness()
    person = harness.add_person()
    other = harness.add_person("Bob")
    mine = harness.add_reminder(_reminder(person.id, "Mine", ReminderKind.FOLLOW_UP, _NOW))
    harness.add_reminder(_reminder(other.id, "Theirs", ReminderKind.FOLLOW_UP, _NOW))

    document = harness.brief.execute(person.id)

    assert document is not None
    assert [reminder.id for reminder in document.reminders] == [mine.id]


def test_undated_reminders_sort_after_dated_ones_without_a_timezone_guess() -> None:
    harness = _Harness()
    person = harness.add_person()
    naive = harness.add_reminder(
        _reminder(person.id, "Naive", ReminderKind.FOLLOW_UP, datetime(2026, 5, 1, 9, 0))
    )
    aware = harness.add_reminder(
        _reminder(person.id, "Aware", ReminderKind.FOLLOW_UP, datetime(2026, 4, 1, 9, 0, tzinfo=UTC))
    )
    undated = harness.add_reminder(_reminder(person.id, "Note", ReminderKind.COMMUNICATION_NOTE))

    document = harness.brief.execute(person.id)

    assert document is not None
    # A naive and an aware datetime cannot be compared as instants; ordering the stored
    # spelling keeps the key total without reading either one in the host timezone.
    assert [reminder.id for reminder in document.reminders] == [aware.id, naive.id, undated.id]


def test_include_sensitive_widens_context_but_never_guidance() -> None:
    harness = _Harness()
    person = harness.add_person()
    ordinary_fact = Fact(
        person_id=person.id, predicate="role", value="Engineer", provenance=_PROVENANCE, recorded_at=_NOW
    )
    sensitive_fact = Fact(
        person_id=person.id,
        predicate="health",
        value="Elevated detail",
        sensitivity=Sensitivity.SENSITIVE,
        provenance=_PROVENANCE,
        recorded_at=_NOW,
    )
    ordinary_interaction = Interaction(
        summary="Coffee", occurred_at=_NOW, participant_ids=[person.id], provenance=_PROVENANCE
    )
    restricted_interaction = Interaction(
        summary="Elevated conversation",
        occurred_at=_NOW,
        participant_ids=[person.id],
        sensitivity=Sensitivity.RESTRICTED,
        provenance=_PROVENANCE,
    )
    ordinary_trait = Trait(
        person_id=person.id,
        category=TraitCategory.COMMUNICATION_STYLE,
        value="Prefers concise writing",
        provenance=_PROVENANCE,
    )
    sensitive_trait = Trait(
        person_id=person.id,
        category=TraitCategory.TOPICS_TO_AVOID,
        value="Elevated topic",
        sensitivity=Sensitivity.RESTRICTED,
        provenance=_PROVENANCE,
    )
    harness.context.facts.extend([ordinary_fact, sensitive_fact])
    harness.context.interactions.extend([ordinary_interaction, restricted_interaction])
    harness.context.traits.extend([ordinary_trait, sensitive_trait])

    ordinary = harness.brief.execute(person.id)
    widened = harness.brief.execute(person.id, include_sensitive=True)

    assert ordinary is not None and widened is not None
    assert [fact.id for fact in ordinary.facts] == [ordinary_fact.id]
    assert {fact.id for fact in widened.facts} == {ordinary_fact.id, sensitive_fact.id}
    assert [record.id for record in ordinary.interactions] == [ordinary_interaction.id]
    assert {record.id for record in widened.interactions} == {
        ordinary_interaction.id,
        restricted_interaction.id,
    }
    assert [trait.id for trait in ordinary.traits] == [ordinary_trait.id]
    assert {trait.id for trait in widened.traits} == {ordinary_trait.id, sensitive_trait.id}

    # Guidance has no sensitivity parameter at all, so both modes see the same ordinary rows.
    for document in (ordinary, widened):
        assert document.guidance.disclosure is DisclosureLevel.ORDINARY
        assert [trait.id for traits in document.guidance.traits.values() for trait in traits] == [
            ordinary_trait.id
        ]
        assert document.guidance.friction_notes == [ordinary_interaction.summary]

    assert ordinary.disclosure.context is DisclosureLevel.ORDINARY
    assert ordinary.disclosure.include_sensitive is False
    assert widened.disclosure.context is DisclosureLevel.SENSITIVE
    assert widened.disclosure.include_sensitive is True


def test_communication_philosophy_travels_into_the_brief() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.preferences.set(PREF_COMMUNICATION_PHILOSOPHY, "Be direct and warm.")

    document = harness.brief.execute(person.id)

    assert document is not None
    assert document.guidance.communication_philosophy == "Be direct and warm."


def test_collections_are_ordered_independently_of_reader_order() -> None:
    harness = _Harness()
    person = harness.add_person(aliases=[Alias(value="Ali", kind=AliasKind.NICKNAME)])
    zoe = harness.add_person("Zoe")
    adam = harness.add_person("adam")
    for other, name in ((zoe, "Zoe"), (adam, "adam")):
        harness.context.relationships.append(
            RelationshipRecord(
                relationship=Relationship(
                    subject_id=other.id,
                    object_id=person.id,
                    type="colleague_of",
                    provenance=_PROVENANCE,
                    created_at=_NOW,
                ),
                other_person_id=other.id,
                other_person_name=name,
                display_type="colleague",
            )
        )
    for org, role in (("Zeta", "Advisor"), ("Acme", "Engineer")):
        harness.context.affiliations.append(
            AffiliationRecord(
                affiliation=Affiliation(
                    person_id=person.id, org_id=org, role=role, provenance=_PROVENANCE, created_at=_NOW
                ),
                organization_name=org,
            )
        )
    harness.context.traits.extend(
        [
            Trait(
                person_id=person.id,
                category=TraitCategory.VALUES,
                value="Craft",
                provenance=_PROVENANCE,
            ),
            Trait(
                person_id=person.id,
                category=TraitCategory.COMMUNICATION_STYLE,
                value="Zebra-length replies",
                provenance=_PROVENANCE,
            ),
            Trait(
                person_id=person.id,
                category=TraitCategory.COMMUNICATION_STYLE,
                value="Answers quickly",
                provenance=_PROVENANCE,
            ),
        ]
    )

    document = harness.brief.execute(person.id)
    reversed_harness_document = _reversed_reader_document(harness, person.id)

    assert document is not None and reversed_harness_document is not None
    # Case folding makes "adam" precede "Zoe"; a raw comparison would not.
    assert [record.other_person_name for record in document.relationships] == ["adam", "Zoe"]
    assert [record.organization_name for record in document.affiliations] == ["Acme", "Zeta"]
    assert [trait.value for trait in document.traits] == [
        "Answers quickly",
        "Zebra-length replies",
        "Craft",
    ]
    assert render_brief_json(document) == render_brief_json(reversed_harness_document)


def _reversed_reader_document(harness: _Harness, person_id: str) -> PersonBriefDocument | None:
    """Recompose the same brief after reversing every reader's row order."""
    harness.context.relationships.reverse()
    harness.context.affiliations.reverse()
    harness.context.traits.reverse()
    harness.context.facts.reverse()
    harness.context.interactions.reverse()
    return harness.brief.execute(person_id)


def test_json_document_is_versioned_labelled_and_reparseable() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.add_reminder(_reminder(person.id, "Send the notes", ReminderKind.FOLLOW_UP, _NOW))

    document = harness.brief.execute(person.id, include_sensitive=True)

    assert document is not None
    text = render_brief_json(document)
    assert text.endswith("\n")
    payload = json.loads(text)
    assert payload["format"] == BRIEF_FORMAT == "people-context-brief"
    assert payload["version"] == BRIEF_VERSION == 1
    assert payload["generated_at"] == "2026-03-04T05:06:00Z"
    assert payload["disclosure"] == {
        "include_sensitive": True,
        "context": "sensitive",
        "guidance": "ordinary",
        "notice": document.disclosure.notice,
    }
    assert "outside the server's disclosure controls" in payload["disclosure"]["notice"]
    assert payload["person"]["id"] == person.id
    # An unknown field must be tolerable by a reader, which is the additive promise the
    # document makes; parsing back drops it rather than failing.
    payload["future_field"] = "ignored"
    assert PersonBriefDocument.model_validate(payload).person.id == person.id


def test_markdown_labels_both_disclosure_levels_and_keeps_empty_sections() -> None:
    harness = _Harness()
    person = harness.add_person()

    document = harness.brief.execute(person.id, include_sensitive=True)

    assert document is not None
    text = render_brief_markdown(document)
    assert text.startswith("# Alice Zhang\n")
    assert text.endswith("\n")
    assert "- **Context disclosure:** sensitive" in text
    assert "- **Guidance disclosure:** ordinary (never widened)" in text
    assert "outside the server's disclosure controls" in text
    for heading in ("## Relationships", "## Affiliations", "## Facts", "## Interactions", "## Traits"):
        assert heading in text
    assert "## Reminders" in text
    assert "## Communication guidance (ordinary disclosure)" in text
    # Relationships, affiliations, facts, interactions, traits, reminders, guidance traits,
    # and interaction notes all stay visible when empty.
    assert text.count("_None recorded._") == 8
    assert "Philosophy: (none set)" in text


def test_rendering_is_byte_stable_for_unchanged_data() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.add_reminder(_reminder(person.id, "Send the notes", ReminderKind.FOLLOW_UP, _NOW))

    first = harness.brief.execute(person.id)
    second = harness.brief.execute(person.id)

    assert first is not None and second is not None
    assert render_brief_json(first) == render_brief_json(second)
    assert render_brief_markdown(first) == render_brief_markdown(second)
