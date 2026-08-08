"""Tests for minimal-disclosure person context retrieval."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from people_context.app.context.query import GetPersonContext
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.person import Alias, Person
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.context import AffiliationRecord, RelationshipRecord
from tests.app.fakes import FakeClock, FakeContextReader, FakePeopleRepository
from tests.conftest import HOST_TIMEZONE_UTC_MINUS_12

_NOW = datetime(2025, 1, 10, 12, tzinfo=UTC)
_PROVENANCE = Provenance(source="test")


def _person(name: str = "Alice") -> Person:
    return Person(
        canonical_name=name,
        aliases=[Alias(value="Ally")],
        summary="A trusted colleague",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _use_case(
    person: Person | None = None,
) -> tuple[GetPersonContext, FakePeopleRepository, FakeContextReader, Person]:
    repo = FakePeopleRepository()
    target = person or _person()
    repo.save_person(target)
    context = FakeContextReader()
    return GetPersonContext(repo, context, FakeClock(_NOW)), repo, context, target


def test_unknown_and_deleted_people_return_stable_empty_shape() -> None:
    use_case, repo, _, person = _use_case()
    person.deleted_at = _NOW
    repo.save_person(person)

    for person_id in ("unknown", person.id):
        result = use_case.execute(person_id)
        assert result.found is False
        assert result.person_id == person_id
        assert result.identity is None
        assert result.relationships == []
        assert result.affiliations == []
        assert result.facts == []
        assert result.interactions == []
        assert result.observations == []
        assert result.traits == []
        assert result.reminders == []


def test_identity_is_narrow_and_active_context_is_outside_zero_budget() -> None:
    use_case, _, context, person = _use_case()
    other = _person("Bob")
    context.relationships.extend(
        [
            RelationshipRecord(
                relationship=Relationship(
                    subject_id=person.id,
                    object_id=other.id,
                    type="friend_of",
                    period=ValidityPeriod(valid_to=date(2025, 1, 10)),
                    provenance=_PROVENANCE,
                ),
                other_person_id=other.id,
                other_person_name=other.canonical_name,
            ),
            RelationshipRecord(
                relationship=Relationship(
                    subject_id=person.id,
                    object_id="expired",
                    type="former_colleague",
                    period=ValidityPeriod(valid_to=date(2025, 1, 9)),
                    provenance=_PROVENANCE,
                ),
                other_person_id="expired",
                other_person_name="Expired",
            ),
        ]
    )
    context.affiliations.append(
        AffiliationRecord(
            affiliation=Affiliation(
                person_id=person.id,
                org_id="org-1",
                role="Engineer",
                period=ValidityPeriod(valid_from=date(2026, 1, 1)),
                provenance=_PROVENANCE,
            ),
            organization_name="Acme",
        )
    )

    result = use_case.execute(person.id, max_items=0)

    assert result.identity is not None
    assert result.identity.model_dump() == {
        "id": person.id,
        "canonical_name": "Alice",
        "aliases": ["Ally"],
        "summary": "A trusted colleague",
        "is_self": False,
    }
    assert [record.relationship.type for record in result.relationships] == ["friend_of"]
    assert [record.organization_name for record in result.affiliations] == ["Acme"]
    assert result.facts == []
    assert result.interactions == []


def test_facts_and_interactions_share_ranked_budget_and_filter_sensitivity() -> None:
    use_case, _, context, person = _use_case()
    context.facts.extend(
        [
            Fact(
                id="new-public",
                person_id=person.id,
                predicate="location",
                value="Dubai",
                recorded_at=_NOW,
                confidence=0.0,
                sensitivity=Sensitivity.PUBLIC,
                provenance=_PROVENANCE,
            ),
            Fact(
                id="old-personal",
                person_id=person.id,
                predicate="role",
                value="Engineer",
                recorded_at=_NOW - timedelta(days=2),
                confidence=1.0,
                sensitivity=Sensitivity.PERSONAL,
                provenance=_PROVENANCE,
            ),
            Fact(
                id="restricted",
                person_id=person.id,
                predicate="secret",
                value="hidden",
                recorded_at=_NOW + timedelta(days=1),
                sensitivity=Sensitivity.RESTRICTED,
                provenance=_PROVENANCE,
            ),
        ]
    )
    context.interactions.extend(
        [
            Interaction(
                id="middle-interaction",
                summary="Discussed launch",
                occurred_at=_NOW - timedelta(days=1),
                participant_ids=[person.id],
                sensitivity=Sensitivity.PERSONAL,
                provenance=_PROVENANCE,
            ),
            Interaction(
                id="sensitive-interaction",
                summary="Private conversation",
                occurred_at=_NOW + timedelta(days=2),
                participant_ids=[person.id],
                sensitivity=Sensitivity.SENSITIVE,
                provenance=_PROVENANCE,
            ),
        ]
    )

    default = use_case.execute(person.id, max_items=2)
    sensitive = use_case.execute(person.id, max_items=10, include_sensitive=True)

    assert [fact.id for fact in default.facts] == ["new-public"]
    assert [interaction.id for interaction in default.interactions] == ["middle-interaction"]
    assert len(default.facts) + len(default.interactions) == 2
    assert {fact.id for fact in sensitive.facts} == {"new-public", "old-personal", "restricted"}
    assert {interaction.id for interaction in sensitive.interactions} == {
        "middle-interaction",
        "sensitive-interaction",
    }


def test_observations_stay_empty_and_traits_require_communication_purpose() -> None:
    use_case, _, context, person = _use_case()
    context.observations.append(Observation(person_id=person.id, text="Never disclose in M1", provenance=_PROVENANCE))
    context.traits.extend(
        [
            Trait(
                id="public-trait",
                person_id=person.id,
                category=TraitCategory.COMMUNICATION_STYLE,
                value="Prefers concise updates",
                sensitivity=Sensitivity.PERSONAL,
                provenance=_PROVENANCE,
            ),
            Trait(
                id="sensitive-trait",
                person_id=person.id,
                category=TraitCategory.TOPICS_TO_AVOID,
                value="Private topic",
                sensitivity=Sensitivity.SENSITIVE,
                provenance=_PROVENANCE,
            ),
        ]
    )

    unrelated = use_case.execute(person.id, purpose="scheduling")
    communication = use_case.execute(person.id, purpose="Help with COMMUNICATION")
    all_traits = use_case.execute(person.id, purpose="communication", include_sensitive=True)

    assert unrelated.observations == communication.observations == []
    assert unrelated.traits == []
    assert [trait.id for trait in communication.traits] == ["public-trait"]
    assert {trait.id for trait in all_traits.traits} == {"public-trait", "sensitive-trait"}


def test_only_active_communication_note_reminders_are_returned_outside_budget() -> None:
    use_case, _, context, person = _use_case()
    context.reminders.extend(
        [
            Reminder(person_id=person.id, text="Use written updates", kind=ReminderKind.COMMUNICATION_NOTE),
            Reminder(person_id=person.id, text="Follow up", kind=ReminderKind.FOLLOW_UP),
            Reminder(
                person_id=person.id,
                text="Old note",
                kind=ReminderKind.COMMUNICATION_NOTE,
                status=ReminderStatus.COMPLETED,
            ),
        ]
    )

    result = use_case.execute(person.id, max_items=0)

    assert [reminder.text for reminder in result.reminders] == ["Use written updates"]


def test_negative_disclosure_budget_is_rejected_explicitly() -> None:
    use_case, _, _, person = _use_case()

    with pytest.raises(ValueError, match="max_items"):
        use_case.execute(person.id, max_items=-1)


def test_naive_timestamps_rank_as_utc_regardless_of_the_host_timezone(host_timezone) -> None:
    # A naive stored timestamp is supported by the write contract, and `datetime.timestamp()`
    # reads one in the host timezone. Twelve hours west of UTC the naive value below would
    # look newer than the aware one, reversing the ranking and — because facts and
    # interactions share one budget — changing which record survives the cutoff.
    use_case, _, context, person = _use_case()
    naive_interaction = Interaction(
        summary="Naive row",
        occurred_at=datetime(2025, 1, 1, 23, 0),
        participant_ids=[person.id],
        provenance=_PROVENANCE,
    )
    aware_interaction = Interaction(
        summary="Aware row",
        occurred_at=datetime(2025, 1, 2, 1, 0, tzinfo=UTC),
        participant_ids=[person.id],
        provenance=_PROVENANCE,
    )
    context.interactions.extend([naive_interaction, aware_interaction])

    under_utc = use_case.execute(person.id)
    under_utc_cutoff = use_case.execute(person.id, max_items=1)
    host_timezone(HOST_TIMEZONE_UTC_MINUS_12)
    shifted = use_case.execute(person.id)
    shifted_cutoff = use_case.execute(person.id, max_items=1)

    assert [record.summary for record in under_utc.interactions] == ["Aware row", "Naive row"]
    assert [record.summary for record in shifted.interactions] == ["Aware row", "Naive row"]
    assert [record.summary for record in under_utc_cutoff.interactions] == ["Aware row"]
    assert [record.summary for record in shifted_cutoff.interactions] == ["Aware row"]


def test_naive_timestamps_are_returned_unchanged_after_ranking(host_timezone) -> None:
    # Only the ranking normalizes; the emitted record still carries what was stored.
    use_case, _, context, person = _use_case()
    stored = datetime(2025, 1, 1, 23, 0)
    context.interactions.append(
        Interaction(
            summary="Naive row",
            occurred_at=stored,
            participant_ids=[person.id],
            provenance=_PROVENANCE,
        )
    )
    host_timezone(HOST_TIMEZONE_UTC_MINUS_12)

    result = use_case.execute(person.id)

    assert result.interactions[0].occurred_at == stored
    assert result.interactions[0].occurred_at.tzinfo is None
