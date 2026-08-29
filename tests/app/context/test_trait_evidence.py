"""Explaining a trait with its evidence, without disclosing evidence the caller may not see."""

from __future__ import annotations

from datetime import UTC, datetime

from people_context.app.context import GetPersonContext
from people_context.domain.person import Person
from people_context.domain.shared import Provenance, Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.context import TraitEvidenceRecord
from tests.app.fakes import FakeClock, FakeContextReader, FakePeopleRepository

_NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
_PROVENANCE = Provenance(source="agent")


def _fixture() -> tuple[GetPersonContext, Person, Trait, Trait]:
    """A visible and a restricted trait, each grounded in evidence of its own level."""
    people = FakePeopleRepository()
    context = FakeContextReader()
    person = Person(canonical_name="Alice Rivera", created_at=_NOW, updated_at=_NOW)
    people.save_person(person)
    ordinary = Trait(
        person_id=person.id,
        category=TraitCategory.COMMUNICATION_STYLE,
        value="Responds to quantitative evidence",
        provenance=_PROVENANCE,
        updated_at=_NOW,
    )
    restricted = Trait(
        person_id=person.id,
        category=TraitCategory.COMMUNICATION_STYLE,
        value="Avoids discussing the reorganisation",
        sensitivity=Sensitivity.RESTRICTED,
        provenance=_PROVENANCE,
        updated_at=_NOW,
    )
    context.traits.extend([ordinary, restricted])
    context.trait_evidence.extend(
        [
            TraitEvidenceRecord(
                trait_id=ordinary.id,
                evidence_type="observation",
                evidence_id="obs-open",
                sensitivity=Sensitivity.PERSONAL,
            ),
            # An ordinary trait may perfectly well rest on a restricted record. Naming that
            # record would tell an ordinary caller it exists.
            TraitEvidenceRecord(
                trait_id=ordinary.id,
                evidence_type="observation",
                evidence_id="obs-restricted",
                sensitivity=Sensitivity.RESTRICTED,
            ),
            TraitEvidenceRecord(
                trait_id=restricted.id,
                evidence_type="interaction",
                evidence_id="int-restricted",
                sensitivity=Sensitivity.PERSONAL,
            ),
        ]
    )
    return GetPersonContext(people, context, FakeClock(_NOW)), person, ordinary, restricted


def test_a_visible_trait_never_names_restricted_evidence() -> None:
    query, person, ordinary, _restricted = _fixture()

    result = query.execute(person.id, purpose="communication")

    assert [link.evidence_id for link in result.trait_evidence] == ["obs-open"]
    assert all(link.trait_id == ordinary.id for link in result.trait_evidence)


def test_evidence_of_a_trait_this_bundle_withheld_is_absent() -> None:
    """A link must not explain a trait the caller was never shown."""
    query, person, _ordinary, restricted = _fixture()

    result = query.execute(person.id, purpose="communication")

    assert restricted.id not in {trait.id for trait in result.traits}
    assert restricted.id not in {link.trait_id for link in result.trait_evidence}


def test_an_elevated_read_sees_the_evidence_its_traits_rest_on() -> None:
    query, person, ordinary, restricted = _fixture()

    result = query.execute(person.id, purpose="communication", include_sensitive=True)

    assert {(link.trait_id, link.evidence_id) for link in result.trait_evidence} == {
        (ordinary.id, "obs-open"),
        (ordinary.id, "obs-restricted"),
        (restricted.id, "int-restricted"),
    }


def test_a_person_with_no_links_returns_the_bundle_it_always_did() -> None:
    people = FakePeopleRepository()
    context = FakeContextReader()
    person = Person(canonical_name="Bob Chen", created_at=_NOW, updated_at=_NOW)
    people.save_person(person)

    result = GetPersonContext(people, context, FakeClock(_NOW)).execute(person.id)

    assert result.trait_evidence == []
