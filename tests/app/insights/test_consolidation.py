"""Application policy for the bounded consolidation context, against a fake reader."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from people_context.app.insights import (
    DEFAULT_CONSOLIDATION_LIMIT,
    MAX_CONSOLIDATION_EVIDENCE_LINKS,
    MAX_CONSOLIDATION_LIMIT,
    MAX_CONSOLIDATION_SIGNALS,
    MIN_CONSOLIDATION_LIMIT,
    SIGNAL_CONTRADICTORY_FACT,
    SIGNAL_DIVERGENT_TRAIT,
    SIGNAL_DUPLICATE_FACT,
    SIGNAL_DUPLICATE_TRAIT,
    SIGNAL_RESTATED_FACT,
    SIGNAL_SUBJECT_FACT,
    SIGNAL_SUBJECT_TRAIT,
    SIGNAL_SUCCEEDING_FACT,
    ConsolidationContextError,
    GetConsolidationContext,
)
from people_context.domain.person import Person
from people_context.domain.shared import Provenance, Sensitivity
from people_context.ports.consolidation import (
    ConsolidationFactRow,
    ConsolidationObservationRow,
    ConsolidationTraitRow,
    PersonConsolidationReader,
)
from people_context.ports.timeline import ENTRY_INTERACTION, ENTRY_OBSERVATION, TimelineEvidenceRow
from tests.app.fakes import FakePeopleRepository, FakePersonConsolidationReader

ALICE = Person(id="P1", canonical_name="Alice")
_ORDINARY = (Sensitivity.PUBLIC, Sensitivity.PERSONAL)
_RECORDED = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _fact(
    fact_id: str,
    *,
    predicate: str = "role",
    value: str = "engineer",
    valid_from: date | None = None,
    valid_to: date | None = None,
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
) -> ConsolidationFactRow:
    return ConsolidationFactRow(
        fact_id=fact_id,
        predicate=predicate,
        value=value,
        valid_from=valid_from,
        valid_to=valid_to,
        recorded_at=_RECORDED,
        confidence=1.0,
        sensitivity=sensitivity,
        provenance=Provenance(source="agent"),
    )


def _trait(
    trait_id: str,
    *,
    category: str = "communication",
    value: str = "prefers written detail",
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
) -> ConsolidationTraitRow:
    return ConsolidationTraitRow(
        trait_id=trait_id,
        category=category,
        value=value,
        evidence_note="derived from the March review",
        confidence=0.6,
        updated_at=_RECORDED,
        sensitivity=sensitivity,
        provenance=Provenance(source="agent"),
    )


def _observation(
    observation_id: str,
    *,
    text: str = "asked for numbers before agreeing",
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
) -> ConsolidationObservationRow:
    return ConsolidationObservationRow(
        observation_id=observation_id,
        text=text,
        observed_at=_RECORDED,
        sensitivity=sensitivity,
        provenance=Provenance(source="agent"),
    )


def _use_case(
    *,
    facts: list[ConsolidationFactRow] | None = None,
    traits: list[ConsolidationTraitRow] | None = None,
    observations: list[ConsolidationObservationRow] | None = None,
    evidence: dict[str, list[tuple[Sensitivity | None, TimelineEvidenceRow]]] | None = None,
    people: list[Person] | None = None,
) -> tuple[GetConsolidationContext, FakePersonConsolidationReader]:
    repo = FakePeopleRepository()
    for person in people if people is not None else [ALICE]:
        repo.save_person(person)
    reader = FakePersonConsolidationReader(facts, traits, observations, evidence)
    return GetConsolidationContext(repo, reader), reader


def test_the_fake_reader_satisfies_the_declared_port() -> None:
    assert isinstance(FakePersonConsolidationReader(), PersonConsolidationReader)


class TestBounds:
    """Each record type carries its own page, and the page is what the reader is asked for."""

    def test_each_record_type_is_cut_at_the_limit_and_reports_that_more_exist(self) -> None:
        use_case, reader = _use_case(
            facts=[_fact(f"f{index}", predicate=f"p{index}") for index in range(4)],
            traits=[_trait(f"t{index}", category=f"c{index}") for index in range(4)],
            observations=[_observation(f"o{index}") for index in range(4)],
        )

        result = use_case.execute(ALICE.id, limit=2)

        assert [fact.fact_id for fact in result.facts] == ["f0", "f1"]
        assert [trait.trait_id for trait in result.traits] == ["t0", "t1"]
        assert [entry.observation_id for entry in result.observations] == ["o0", "o1"]
        assert (result.facts_truncated, result.traits_truncated, result.observations_truncated) == (
            True,
            True,
            True,
        )
        # Every read asks for exactly one row past the page, never for the table.
        assert reader.calls == [
            ("facts", ALICE.id, 2, _ORDINARY),
            ("traits", ALICE.id, 2, _ORDINARY),
            ("observations", ALICE.id, 2, _ORDINARY),
        ]

    def test_one_dense_record_type_does_not_consume_another_type_page(self) -> None:
        """A person with many observations must still have their facts reported."""
        use_case, _ = _use_case(
            facts=[_fact("f1")],
            observations=[_observation(f"o{index}") for index in range(10)],
        )

        result = use_case.execute(ALICE.id, limit=3)

        assert [fact.fact_id for fact in result.facts] == ["f1"]
        assert result.facts_truncated is False
        assert len(result.observations) == 3
        assert result.observations_truncated is True

    def test_an_untruncated_page_says_so(self) -> None:
        use_case, _ = _use_case(facts=[_fact("f1")], traits=[_trait("t1")], observations=[_observation("o1")])

        result = use_case.execute(ALICE.id)

        assert (result.facts_truncated, result.traits_truncated, result.observations_truncated) == (
            False,
            False,
            False,
        )
        assert result.limit == DEFAULT_CONSOLIDATION_LIMIT

    @pytest.mark.parametrize("limit", [MIN_CONSOLIDATION_LIMIT - 1, 0, -1, MAX_CONSOLIDATION_LIMIT + 1])
    def test_an_out_of_range_limit_is_refused_rather_than_clamped(self, limit: int) -> None:
        use_case, reader = _use_case(facts=[_fact("f1")])

        with pytest.raises(ConsolidationContextError):
            use_case.execute(ALICE.id, limit=limit)
        assert reader.calls == []

    @pytest.mark.parametrize("limit", [MIN_CONSOLIDATION_LIMIT, MAX_CONSOLIDATION_LIMIT])
    def test_both_range_endpoints_are_accepted(self, limit: int) -> None:
        use_case, _ = _use_case(facts=[_fact("f1")])

        assert use_case.execute(ALICE.id, limit=limit).limit == limit


class TestMissingPerson:
    """An unknown or removed identity is reported, not raised, exactly as the timeline does."""

    def test_an_unknown_person_is_reported_as_not_found_without_reading(self) -> None:
        use_case, reader = _use_case(facts=[_fact("f1")])

        result = use_case.execute("nobody")

        assert result.found is False
        assert (result.facts, result.traits, result.observations, result.signals) == ([], [], [], [])
        assert reader.calls == []

    def test_a_soft_deleted_person_is_reported_as_not_found(self) -> None:
        removed = Person(id="P9", canonical_name="Gone", deleted_at=_RECORDED)
        use_case, reader = _use_case(facts=[_fact("f1")], people=[removed])

        assert use_case.execute(removed.id).found is False
        assert reader.calls == []


class TestFactSignals:
    """Two facts sharing a predicate are related by value equality and period overlap."""

    def test_same_value_over_overlapping_days_is_a_duplicate(self) -> None:
        use_case, _ = _use_case(
            facts=[
                _fact("f1", valid_from=date(2026, 1, 1)),
                _fact("f2", valid_from=date(2026, 3, 1)),
            ]
        )

        assert [signal.model_dump() for signal in use_case.execute(ALICE.id).signals] == [
            {
                "kind": SIGNAL_DUPLICATE_FACT,
                "entity_type": SIGNAL_SUBJECT_FACT,
                "key": "role",
                "entity_ids": ["f1", "f2"],
            }
        ]

    def test_same_value_over_disjoint_days_is_a_restatement(self) -> None:
        use_case, _ = _use_case(
            facts=[
                _fact("f1", valid_from=date(2020, 1, 1), valid_to=date(2020, 12, 31)),
                _fact("f2", valid_from=date(2026, 1, 1)),
            ]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_RESTATED_FACT]

    def test_different_values_over_overlapping_days_contradict(self) -> None:
        use_case, _ = _use_case(
            facts=[
                _fact("f1", value="engineer", valid_from=date(2026, 1, 1)),
                _fact("f2", value="architect", valid_from=date(2026, 6, 1)),
            ]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_CONTRADICTORY_FACT]

    def test_different_values_over_disjoint_days_are_a_succession(self) -> None:
        """This is what a well-formed supersession leaves behind, so it is history, not a defect."""
        use_case, _ = _use_case(
            facts=[
                _fact("f1", value="engineer", valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
                _fact("f2", value="architect", valid_from=date(2026, 7, 1)),
            ]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_SUCCEEDING_FACT]

    def test_two_open_ended_facts_always_overlap(self) -> None:
        use_case, _ = _use_case(facts=[_fact("f1", value="engineer"), _fact("f2", value="architect")])

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_CONTRADICTORY_FACT]

    def test_adjacent_inclusive_periods_do_not_overlap(self) -> None:
        """Endpoints are inclusive, so a period ending on the day before the next one starts is clear."""
        use_case, _ = _use_case(
            facts=[
                _fact("f1", value="engineer", valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)),
                _fact("f2", value="engineer", valid_from=date(2026, 7, 1), valid_to=date(2026, 12, 31)),
            ]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_RESTATED_FACT]

    def test_periods_sharing_exactly_one_day_overlap(self) -> None:
        use_case, _ = _use_case(
            facts=[
                _fact("f1", value="engineer", valid_from=date(2026, 1, 1), valid_to=date(2026, 7, 1)),
                _fact("f2", value="engineer", valid_from=date(2026, 7, 1), valid_to=date(2026, 12, 31)),
            ]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_DUPLICATE_FACT]

    def test_facts_under_different_predicates_are_never_related(self) -> None:
        use_case, _ = _use_case(facts=[_fact("f1", predicate="role"), _fact("f2", predicate="city")])

        assert use_case.execute(ALICE.id).signals == []

    def test_predicates_and_values_are_compared_by_the_projects_name_normalization(self) -> None:
        """Casing, spacing, and combining marks are not differences in meaning."""
        use_case, _ = _use_case(
            facts=[
                _fact("f1", predicate="Employer", value="Café  Roma"),
                _fact("f2", predicate="employer", value="cafe roma"),
            ]
        )

        signals = use_case.execute(ALICE.id).signals

        assert [signal.kind for signal in signals] == [SIGNAL_DUPLICATE_FACT]
        assert signals[0].key == "employer"


class TestTraitSignals:
    """Two traits sharing a category are related by value equality alone."""

    def test_same_value_in_one_category_is_a_duplicate(self) -> None:
        use_case, _ = _use_case(traits=[_trait("t1"), _trait("t2")])

        assert [signal.model_dump() for signal in use_case.execute(ALICE.id).signals] == [
            {
                "kind": SIGNAL_DUPLICATE_TRAIT,
                "entity_type": SIGNAL_SUBJECT_TRAIT,
                "key": "communication",
                "entity_ids": ["t1", "t2"],
            }
        ]

    def test_different_values_in_one_category_diverge(self) -> None:
        use_case, _ = _use_case(
            traits=[_trait("t1", value="prefers written detail"), _trait("t2", value="prefers a call")]
        )

        assert [signal.kind for signal in use_case.execute(ALICE.id).signals] == [SIGNAL_DIVERGENT_TRAIT]

    def test_traits_in_different_categories_are_never_related(self) -> None:
        use_case, _ = _use_case(traits=[_trait("t1", category="communication"), _trait("t2", category="working")])

        assert use_case.execute(ALICE.id).signals == []


class TestSignalOrderingAndBounds:
    """Signals are deterministic, reported once per pair, and bounded."""

    def test_pairs_are_ordered_by_type_then_key_then_ascending_ids(self) -> None:
        use_case, _ = _use_case(
            facts=[
                _fact("f2", predicate="role"),
                _fact("f1", predicate="role"),
                _fact("f3", predicate="city", value="Berlin"),
                _fact("f4", predicate="city", value="Berlin"),
            ],
            traits=[_trait("t2"), _trait("t1")],
        )

        signals = use_case.execute(ALICE.id).signals

        assert [(signal.entity_type, signal.key, tuple(signal.entity_ids)) for signal in signals] == [
            (SIGNAL_SUBJECT_FACT, "city", ("f3", "f4")),
            (SIGNAL_SUBJECT_FACT, "role", ("f1", "f2")),
            (SIGNAL_SUBJECT_TRAIT, "communication", ("t1", "t2")),
        ]

    def test_a_group_of_three_reports_each_pair_once(self) -> None:
        use_case, _ = _use_case(facts=[_fact("f1"), _fact("f2"), _fact("f3")])

        assert [tuple(signal.entity_ids) for signal in use_case.execute(ALICE.id).signals] == [
            ("f1", "f2"),
            ("f1", "f3"),
            ("f2", "f3"),
        ]

    def test_signals_are_capped_and_the_cap_is_reported(self) -> None:
        """One dense group must not turn a bounded page into a quadratic response."""
        use_case, _ = _use_case(facts=[_fact(f"f{index:03d}") for index in range(MAX_CONSOLIDATION_LIMIT)])

        result = use_case.execute(ALICE.id, limit=MAX_CONSOLIDATION_LIMIT)

        assert len(result.signals) == MAX_CONSOLIDATION_SIGNALS
        assert result.signals_truncated is True
        # The surviving prefix is the head of the one deterministic order, not an arbitrary sample.
        assert [tuple(signal.entity_ids) for signal in result.signals[:2]] == [
            ("f000", "f001"),
            ("f000", "f002"),
        ]

    def test_a_page_below_the_cap_is_not_marked_truncated(self) -> None:
        use_case, _ = _use_case(facts=[_fact("f1"), _fact("f2")])

        result = use_case.execute(ALICE.id)

        assert len(result.signals) == 1
        assert result.signals_truncated is False


class TestEvidence:
    """Trait citations and their observation back-references."""

    def test_a_trait_reports_the_records_it_rests_on(self) -> None:
        use_case, reader = _use_case(
            traits=[_trait("t1")],
            evidence={
                "t1": [
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o1")),
                    (Sensitivity.PUBLIC, TimelineEvidenceRow(evidence_type=ENTRY_INTERACTION, evidence_id="i1")),
                ]
            },
        )

        trait = use_case.execute(ALICE.id).traits[0]

        assert [(link.evidence_type, link.evidence_id) for link in trait.evidence] == [
            (ENTRY_OBSERVATION, "o1"),
            (ENTRY_INTERACTION, "i1"),
        ]
        assert trait.evidence_truncated is False
        assert reader.evidence_calls == [("t1", MAX_CONSOLIDATION_EVIDENCE_LINKS, _ORDINARY)]

    def test_evidence_the_caller_may_not_read_is_neither_named_nor_counted(self) -> None:
        """Naming a withheld citation, or counting it towards truncation, discloses that it exists."""
        use_case, _ = _use_case(
            traits=[_trait("t1")],
            evidence={
                "t1": [
                    (Sensitivity.RESTRICTED, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o1")),
                ]
            },
        )

        trait = use_case.execute(ALICE.id).traits[0]

        assert trait.evidence == []
        assert trait.evidence_truncated is False

    def test_a_trait_carrying_more_links_than_the_ceiling_says_so(self) -> None:
        links = [
            (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id=f"o{index}"))
            for index in range(MAX_CONSOLIDATION_EVIDENCE_LINKS + 1)
        ]
        use_case, _ = _use_case(traits=[_trait("t1")], evidence={"t1": links})

        trait = use_case.execute(ALICE.id).traits[0]

        assert len(trait.evidence) == MAX_CONSOLIDATION_EVIDENCE_LINKS
        assert trait.evidence_truncated is True

    def test_several_observations_supporting_one_trait_stay_separate_evidence(self) -> None:
        """The reverse links exist so a reader can tell repeated evidence from a repeated record."""
        use_case, _ = _use_case(
            traits=[_trait("t1")],
            observations=[_observation("o1"), _observation("o2", text="pushed back on the timeline")],
            evidence={
                "t1": [
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o1")),
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o2")),
                ]
            },
        )

        result = use_case.execute(ALICE.id)

        assert [entry.cited_by_trait_ids for entry in result.observations] == [["t1"], ["t1"]]
        # Two observations under one trait are two pieces of evidence, never a duplicate signal.
        assert result.signals == []

    def test_an_uncited_observation_reports_no_citing_trait(self) -> None:
        use_case, _ = _use_case(traits=[_trait("t1")], observations=[_observation("o1")])

        assert use_case.execute(ALICE.id).observations[0].cited_by_trait_ids == []

    def test_citing_trait_ids_are_sorted_and_ignore_interaction_citations(self) -> None:
        use_case, _ = _use_case(
            traits=[_trait("t2"), _trait("t1", category="working")],
            observations=[_observation("o1")],
            evidence={
                "t1": [
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o1")),
                ],
                "t2": [
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_OBSERVATION, evidence_id="o1")),
                    (Sensitivity.PERSONAL, TimelineEvidenceRow(evidence_type=ENTRY_INTERACTION, evidence_id="o1")),
                ],
            },
        )

        assert use_case.execute(ALICE.id).observations[0].cited_by_trait_ids == ["t1", "t2"]


class TestDisclosure:
    """Ordinary reads are filtered in the reader, before the page is cut."""

    def test_an_ordinary_read_asks_only_for_ordinary_levels(self) -> None:
        use_case, reader = _use_case(
            facts=[_fact("f1"), _fact("f2", sensitivity=Sensitivity.RESTRICTED)],
            traits=[_trait("t1", sensitivity=Sensitivity.SENSITIVE)],
            observations=[_observation("o1", sensitivity=Sensitivity.SENSITIVE)],
        )

        result = use_case.execute(ALICE.id)

        assert [fact.fact_id for fact in result.facts] == ["f1"]
        assert (result.traits, result.observations) == ([], [])
        assert result.include_sensitive is False
        assert {call[3] for call in reader.calls} == {_ORDINARY}

    def test_an_elevated_record_cannot_change_a_signal_it_does_not_appear_in(self) -> None:
        """A withheld fact must not silently contradict a visible one."""
        use_case, _ = _use_case(
            facts=[
                _fact("f1", value="engineer"),
                _fact("f2", value="architect", sensitivity=Sensitivity.RESTRICTED),
            ]
        )

        assert use_case.execute(ALICE.id).signals == []

    def test_the_explicit_opt_in_widens_every_read_and_says_so(self) -> None:
        use_case, reader = _use_case(
            facts=[_fact("f1"), _fact("f2", sensitivity=Sensitivity.RESTRICTED)],
        )

        result = use_case.execute(ALICE.id, include_sensitive=True)

        assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]
        assert result.include_sensitive is True
        assert {call[3] for call in reader.calls} == {
            (Sensitivity.PUBLIC, Sensitivity.PERSONAL, Sensitivity.SENSITIVE, Sensitivity.RESTRICTED)
        }


class TestProjection:
    """The read carries the durable records' own values and provenance, unchanged."""

    def test_fact_rows_are_projected_field_for_field(self) -> None:
        row = _fact("f1", valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))
        use_case, _ = _use_case(facts=[row])

        fact = use_case.execute(ALICE.id).facts[0]

        assert fact.model_dump() == {
            "fact_id": "f1",
            "predicate": "role",
            "value": "engineer",
            "valid_from": date(2026, 1, 1),
            "valid_to": date(2026, 12, 31),
            "recorded_at": _RECORDED,
            "confidence": 1.0,
            "sensitivity": Sensitivity.PERSONAL,
            "provenance": {"source": "agent", "session": None, "stated_by": None},
            "source_session_id": None,
        }

    def test_provenance_receipts_travel_with_every_record_type(self) -> None:
        use_case, _ = _use_case(
            facts=[
                ConsolidationFactRow(
                    fact_id="f1",
                    predicate="role",
                    value="engineer",
                    valid_from=None,
                    valid_to=None,
                    recorded_at=_RECORDED,
                    confidence=1.0,
                    sensitivity=Sensitivity.PERSONAL,
                    provenance=Provenance(source="import", session="msg-1", stated_by="alice"),
                    source_session_id="S1",
                )
            ],
            traits=[
                ConsolidationTraitRow(
                    trait_id="t1",
                    category="communication",
                    value="brief",
                    evidence_note=None,
                    confidence=0.4,
                    updated_at=_RECORDED,
                    sensitivity=Sensitivity.PERSONAL,
                    provenance=Provenance(source="agent"),
                    source_session_id="S2",
                )
            ],
            observations=[
                ConsolidationObservationRow(
                    observation_id="o1",
                    text="asked twice",
                    observed_at=_RECORDED,
                    sensitivity=Sensitivity.PERSONAL,
                    provenance=Provenance(source="operator"),
                    source_session_id="S3",
                )
            ],
        )

        result = use_case.execute(ALICE.id)

        assert result.facts[0].source_session_id == "S1"
        assert result.traits[0].source_session_id == "S2"
        assert result.observations[0].source_session_id == "S3"
        assert result.traits[0].evidence_note is None
        # Stored provenance travels beside the receipt and is not folded into it.
        assert result.facts[0].provenance == Provenance(source="import", session="msg-1", stated_by="alice")
        assert result.traits[0].provenance.source == "agent"
        assert result.observations[0].provenance.source == "operator"

    def test_a_directly_recorded_record_still_reports_who_asserted_it(self) -> None:
        """No import receipt is not the same as no provenance.

        A maintenance proposal argues about which of two competing records to believe, and who
        asserted each is half of that argument. A record entered by hand has `source_session_id`
        null, so provenance is the only thing that can answer it — for every record type alike,
        not just for facts.
        """
        use_case, _ = _use_case(
            facts=[_fact("f1")],
            traits=[_trait("t1")],
            observations=[_observation("o1")],
        )

        result = use_case.execute(ALICE.id)

        for record in (result.facts[0], result.traits[0], result.observations[0]):
            assert record.source_session_id is None
            assert record.provenance.source == "agent"


def test_the_read_performs_no_mutation() -> None:
    """A report path must not write; the use case is given no writer to write with."""
    repo = FakePeopleRepository()
    repo.save_person(ALICE)
    reader = FakePersonConsolidationReader([_fact("f1")], [_trait("t1")], [_observation("o1")])
    use_case = GetConsolidationContext(repo, reader)
    before = repo.list_people()

    use_case.execute(ALICE.id)

    assert repo.list_people() == before
    assert not hasattr(use_case, "_writer")
    assert not hasattr(use_case, "_audit")
