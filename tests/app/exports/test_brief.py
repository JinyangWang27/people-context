"""Application policy for the deterministic person brief (M14.1)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

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
from people_context.app.insights import (
    DEFAULT_TIMELINE_LIMIT,
    MAX_TIMELINE_LIMIT,
    MIN_TIMELINE_LIMIT,
    GetPersonTimeline,
    PersonTimelineError,
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
from people_context.ports.timeline import (
    ENTRY_INTERACTION,
    ENTRY_TRAIT,
    TimelineEvidenceRow,
    TimelineRow,
)
from tests.app.fakes import (
    FakeClock,
    FakeContextReader,
    FakePeopleRepository,
    FakePersonTimelineReader,
    FakePreferencesStore,
    FakeRecordStore,
)
from tests.conftest import HOST_TIMEZONE_UTC_MINUS_12

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
        self.timeline = FakePersonTimelineReader()
        self.brief = ComposePersonBrief(
            GetPersonContext(self.people, self.context, self.clock),
            GetCommunicationGuidance(self.people, self.context, self.preferences, self.clock),
            ListReminders(self.records),
            GetPersonTimeline(self.people, self.timeline),
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
    naive = harness.add_reminder(_reminder(person.id, "Naive", ReminderKind.FOLLOW_UP, datetime(2026, 5, 1, 9, 0)))
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
        assert [trait.id for traits in document.guidance.traits.values() for trait in traits] == [ordinary_trait.id]
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
        "history": None,
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


def test_document_is_identical_under_a_non_utc_host_timezone(host_timezone) -> None:
    # The JSON document promises deterministic ordering, so nothing in it may depend on the
    # machine that produced it. Naive stored timestamps are the case that can: they reach
    # both the shared context budget and the friction-note limit through instant comparisons.
    harness = _Harness()
    person = harness.add_person()
    for index, occurred_at in enumerate((datetime(2026, 1, 1, 23, 0), datetime(2026, 1, 2, 1, 0, tzinfo=UTC))):
        harness.context.interactions.append(
            Interaction(
                summary=f"Row {index}",
                occurred_at=occurred_at,
                participant_ids=[person.id],
                provenance=_PROVENANCE,
            )
        )
    harness.context.facts.append(
        Fact(
            person_id=person.id,
            predicate="role",
            value="Engineer",
            provenance=_PROVENANCE,
            recorded_at=datetime(2026, 1, 1, 22, 0),
        )
    )
    harness.add_reminder(_reminder(person.id, "Send the notes", ReminderKind.FOLLOW_UP, _NOW))

    under_utc = render_brief_json(harness.brief.execute(person.id))
    host_timezone(HOST_TIMEZONE_UTC_MINUS_12)
    shifted = render_brief_json(harness.brief.execute(person.id))

    assert under_utc == shifted


def test_rendering_is_byte_stable_for_unchanged_data() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.add_reminder(_reminder(person.id, "Send the notes", ReminderKind.FOLLOW_UP, _NOW))

    first = harness.brief.execute(person.id)
    second = harness.brief.execute(person.id)

    assert first is not None and second is not None
    assert render_brief_json(first) == render_brief_json(second)
    assert render_brief_markdown(first) == render_brief_markdown(second)


# --- Opt-in history (M23.1) -------------------------------------------------------------------


def _timeline_row(
    entry_id: str,
    effective_at: datetime,
    *,
    entry_type: str = ENTRY_INTERACTION,
    basis: str = "occurred_at",
    summary: str = "Coffee",
    sensitivity: Sensitivity | None = Sensitivity.PERSONAL,
    **fields: object,
) -> TimelineRow:
    return TimelineRow(
        entry_type=entry_type,
        entry_id=entry_id,
        effective_at=effective_at,
        basis=basis,
        summary=summary,
        sensitivity=sensitivity,
        **fields,
    )


def test_an_ordinary_brief_reads_no_history_at_all() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.timeline.rows.append(_timeline_row("t1", _NOW))

    document = harness.brief.execute(person.id)

    assert document is not None
    # "Not requested" is a different fact from "requested and empty", and costs no read.
    assert document.history is None
    assert document.disclosure.history is None
    assert harness.timeline.calls == []
    assert "## History" not in render_brief_markdown(document)


def test_requested_but_empty_history_is_an_empty_page_rather_than_null() -> None:
    harness = _Harness()
    person = harness.add_person()

    document = harness.brief.execute(person.id, include_history=True)

    assert document is not None
    assert document.history is not None
    assert document.history.entries == []
    assert document.history.truncated is False
    assert document.history.limit == DEFAULT_TIMELINE_LIMIT == 50
    assert len(harness.timeline.calls) == 1
    assert "## History (newest first, limit 50, ordinary disclosure)" in render_brief_markdown(document)


def test_history_limit_defaults_and_is_bounded_at_both_ends() -> None:
    harness = _Harness()
    person = harness.add_person()

    for limit in (MIN_TIMELINE_LIMIT, MAX_TIMELINE_LIMIT):
        document = harness.brief.execute(person.id, include_history=True, history_limit=limit)
        assert document is not None and document.history is not None
        assert document.history.limit == limit

    for rejected in (MIN_TIMELINE_LIMIT - 1, MAX_TIMELINE_LIMIT + 1):
        with pytest.raises(PersonTimelineError):
            harness.brief.execute(person.id, include_history=True, history_limit=rejected)

    # An ordinary brief never applies the bound, so it never fails on one either.
    assert harness.brief.execute(person.id, history_limit=0) is not None


def test_a_longer_history_reports_its_truncation_in_both_renderings() -> None:
    harness = _Harness()
    person = harness.add_person()
    for index in range(3):
        harness.timeline.rows.append(
            _timeline_row(f"t{index}", _NOW - timedelta(days=index), summary=f"Row {index}")
        )

    document = harness.brief.execute(person.id, include_history=True, history_limit=2)

    assert document is not None and document.history is not None
    assert document.history.truncated is True
    assert [entry.entry_id for entry in document.history.entries] == ["t0", "t1"]
    assert json.loads(render_brief_json(document))["history"]["truncated"] is True
    assert "bounded page, not a complete history" in render_brief_markdown(document)


def test_history_entries_keep_basis_validity_and_source_reference() -> None:
    harness = _Harness()
    person = harness.add_person()
    # Recorded long after the period it describes: the entry must show both, and say which
    # of the two `effective_at` came from.
    harness.timeline.rows.append(
        _timeline_row(
            "a1",
            datetime(2020, 1, 1, tzinfo=UTC),
            entry_type="affiliation",
            basis="valid_from",
            summary="Engineer at Acme",
            sensitivity=None,
            valid_from=date(2020, 1, 1),
            valid_to=date(2023, 6, 30),
            source_session_id="sess-1",
        )
    )
    harness.timeline.rows.append(
        _timeline_row("f1", datetime(2026, 2, 1, tzinfo=UTC), entry_type="fact", basis="recorded_at")
    )

    document = harness.brief.execute(person.id, include_history=True)

    assert document is not None and document.history is not None
    # Newest first, by the instant each was placed by.
    assert [entry.entry_id for entry in document.history.entries] == ["f1", "a1"]
    affiliation = document.history.entries[1]
    assert affiliation.basis == "valid_from"
    assert (affiliation.valid_from, affiliation.valid_to) == (date(2020, 1, 1), date(2023, 6, 30))
    assert affiliation.source_session_id == "sess-1"
    text = render_brief_markdown(document)
    assert "2020-01-01T00:00:00+00:00 (by valid_from) — affiliation: Engineer at Acme" in text
    assert "valid 2020-01-01 to 2023-06-30" in text
    assert "source session sess-1" in text
    assert "2026-02-01T00:00:00+00:00 (by recorded_at) — fact:" in text


def test_include_sensitive_widens_history_and_labels_it_without_touching_guidance() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.timeline.rows.append(
        _timeline_row("r1", _NOW, summary="Restricted note", sensitivity=Sensitivity.RESTRICTED)
    )

    ordinary = harness.brief.execute(person.id, include_history=True)
    widened = harness.brief.execute(person.id, include_sensitive=True, include_history=True)

    assert ordinary is not None and ordinary.history is not None
    assert widened is not None and widened.history is not None
    assert ordinary.history.entries == []
    assert ordinary.disclosure.history is DisclosureLevel.ORDINARY
    assert [entry.entry_id for entry in widened.history.entries] == ["r1"]
    assert widened.disclosure.history is DisclosureLevel.SENSITIVE
    assert widened.disclosure.guidance is DisclosureLevel.ORDINARY
    assert "limit 50, sensitive disclosure" in render_brief_markdown(widened)


def test_restricted_evidence_of_a_visible_trait_is_neither_cited_nor_signalled() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.timeline.rows.append(
        _timeline_row("tr1", _NOW, entry_type=ENTRY_TRAIT, basis="updated_at", summary="Direct")
    )
    harness.timeline.evidence["tr1"] = [
        (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type="interaction", evidence_id="i1")),
        (Sensitivity.RESTRICTED, TimelineEvidenceRow(evidence_type="observation", evidence_id="o9")),
    ]

    document = harness.brief.execute(person.id, include_history=True)

    assert document is not None and document.history is not None
    entry = document.history.entries[0]
    assert [(link.evidence_type, link.evidence_id) for link in entry.evidence] == [("interaction", "i1")]
    # Naming the withheld citation, or flagging that one exists, would disclose the record.
    assert entry.evidence_truncated is False
    text = render_brief_markdown(document)
    assert "evidence: interaction i1" in text
    assert "o9" not in text


def test_composing_a_brief_with_history_still_writes_nothing() -> None:
    harness = _Harness()
    person = harness.add_person()
    harness.timeline.rows.append(_timeline_row("t1", _NOW))
    before = dict(harness.records.records)

    assert harness.brief.execute(person.id, include_history=True) is not None

    # There is no audit log or changelog to inspect because the use case is handed neither:
    # what a composed history can touch at all is the record store, and it does not.
    assert harness.records.records == before
