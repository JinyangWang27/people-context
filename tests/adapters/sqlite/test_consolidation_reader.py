"""Real-SQLite behaviour for the bounded person consolidation reader."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqlitePersonConsolidationReader,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.sqlite.consolidation_reader import (
    _FACTS_SQL,
    _OBSERVATIONS_SQL,
    _TRAITS_SQL,
)
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.insights import GetConsolidationContext
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
    RecordTrait,
    RecordTraitInput,
)
from people_context.domain.fact import Fact
from people_context.domain.observation import Observation
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.clock import SystemClock
from people_context.ports.consolidation import PersonConsolidationReader
from people_context.ports.timeline import ENTRY_INTERACTION, ENTRY_OBSERVATION

ORDINARY = (Sensitivity.PUBLIC, Sensitivity.PERSONAL)
EVERY_LEVEL = (Sensitivity.PUBLIC, Sensitivity.PERSONAL, Sensitivity.SENSITIVE, Sensitivity.RESTRICTED)


class _Fixture:
    """A live SQLite database with the writers needed to seed one person's stored knowledge."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection = open_db(":memory:")
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()
        self.trait_evidence = SqliteTraitEvidenceStore(self.conn)
        self.reader = SqlitePersonConsolidationReader(self.conn)
        self.facts = RecordFact(self.people, self.records, self.audit, self.clock)
        self.observations = RecordObservation(self.people, self.records, self.audit, self.clock)
        self.interactions = RecordInteraction(self.people, self.records, self.audit, self.clock)
        self.traits = RecordTrait(self.people, self.records, self.audit, self.clock, self.trait_evidence)

    def person(self, name: str) -> Person:
        person = Person(canonical_name=name)
        self.people.save_person(person)
        return person

    def fact(
        self,
        person_id: str,
        *,
        predicate: str = "employer",
        value: str = "Acme",
        valid_from: date | None = None,
        valid_to: date | None = None,
        confidence: float | None = None,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
        source: str = "agent",
    ) -> Fact:
        return self.facts.execute(
            RecordFactInput(
                person_id=person_id,
                predicate=predicate,
                value=value,
                valid_from=valid_from,
                valid_to=valid_to,
                confidence=confidence,
                sensitivity=sensitivity,
                source=source,
            )
        )

    def observation(
        self,
        person_id: str,
        *,
        text: str = "asked for numbers",
        observed_at: datetime | None = None,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> Observation:
        return self.observations.execute(
            RecordObservationInput(
                person_id=person_id,
                text=text,
                observed_at=observed_at,
                sensitivity=sensitivity,
            )
        )

    def trait(
        self,
        person_id: str,
        *,
        category: TraitCategory = TraitCategory.COMMUNICATION_STYLE,
        value: str = "prefers written detail",
        evidence_note: str | None = "derived from the March review",
        confidence: float | None = 0.6,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
        evidence_ids: list[str] | None = None,
    ) -> Trait:
        return self.traits.execute(
            RecordTraitInput(
                person_id=person_id,
                category=category,
                value=value,
                evidence_note=evidence_note,
                confidence=confidence,
                sensitivity=sensitivity,
                evidence_ids=evidence_ids or [],
            )
        )

    def add_source(self, session_id: str, created_at: str) -> None:
        self.conn.execute(
            "INSERT INTO import_source_sessions (id, source_kind, status, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "email", "committed", created_at),
        )

    def map_candidate(
        self,
        candidate_id: str,
        session_id: str,
        entity_type: str,
        entity_id: str,
        created_at: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO import_candidate_mappings "
            "(candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at) "
            "VALUES (?, ?, ?, 'entity', ?, ?, ?)",
            (candidate_id, "B1", session_id, entity_type, entity_id, created_at),
        )

    def counts(self) -> tuple[int, int]:
        audit = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        changelog = self.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0]
        return audit, changelog


def test_the_sqlite_reader_satisfies_the_declared_port() -> None:
    assert isinstance(SqlitePersonConsolidationReader(open_db(":memory:")), PersonConsolidationReader)


def test_every_stored_field_reaches_the_projection() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    stored = fixture.fact(
        alice.id,
        predicate="employer",
        value="Acme",
        valid_from=date(2024, 1, 1),
        valid_to=date(2026, 12, 31),
        confidence=0.8,
        source="import",
    )

    row = fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY)[0]

    assert row.fact_id == stored.id
    assert (row.predicate, row.value) == ("employer", "Acme")
    assert (row.valid_from, row.valid_to) == (date(2024, 1, 1), date(2026, 12, 31))
    assert row.recorded_at == stored.recorded_at
    assert row.confidence == 0.8
    assert row.sensitivity == Sensitivity.PERSONAL
    assert row.source == "import"
    assert row.source_session_id is None


def test_trait_and_observation_rows_carry_their_own_fields() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    trait = fixture.trait(alice.id, value="prefers a call", evidence_note="from the April sync", confidence=0.3)
    observed_at = datetime(2026, 4, 2, 9, 0, tzinfo=UTC)
    observation = fixture.observation(alice.id, text="pushed back on the timeline", observed_at=observed_at)

    trait_row = fixture.reader.list_consolidation_traits(alice.id, limit=10, sensitivities=ORDINARY)[0]
    observation_row = fixture.reader.list_consolidation_observations(alice.id, limit=10, sensitivities=ORDINARY)[0]

    assert (trait_row.trait_id, trait_row.category, trait_row.value) == (
        trait.id,
        TraitCategory.COMMUNICATION_STYLE.value,
        "prefers a call",
    )
    assert (trait_row.evidence_note, trait_row.confidence) == ("from the April sync", 0.3)
    assert trait_row.updated_at == trait.updated_at
    assert (observation_row.observation_id, observation_row.text) == (observation.id, "pushed back on the timeline")
    assert observation_row.observed_at == observed_at


def test_another_persons_records_are_never_returned() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.fact(bob.id, value="Globex")
    fixture.trait(bob.id)
    fixture.observation(bob.id)

    assert fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY) == []
    assert fixture.reader.list_consolidation_traits(alice.id, limit=10, sensitivities=ORDINARY) == []
    assert fixture.reader.list_consolidation_observations(alice.id, limit=10, sensitivities=ORDINARY) == []


class TestBounding:
    """Every read is bounded in SQL, one row past the page."""

    def test_each_read_returns_exactly_one_row_past_the_limit(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        for index in range(6):
            fixture.fact(alice.id, predicate=f"p{index}")
            fixture.trait(alice.id, value=f"v{index}")
            fixture.observation(alice.id, text=f"note {index}")

        assert len(fixture.reader.list_consolidation_facts(alice.id, limit=2, sensitivities=ORDINARY)) == 3
        assert len(fixture.reader.list_consolidation_traits(alice.id, limit=2, sensitivities=ORDINARY)) == 3
        assert len(fixture.reader.list_consolidation_observations(alice.id, limit=2, sensitivities=ORDINARY)) == 3

    def test_every_query_carries_its_own_limit(self) -> None:
        """A bound applied after the read would let a dense store answer `limit=1` expensively."""
        for sql in (_FACTS_SQL, _TRAITS_SQL, _OBSERVATIONS_SQL):
            assert "LIMIT :limit" in sql

    def test_no_read_scans_a_whole_table(self) -> None:
        """Every branch reaches its rows through an index, so another person's rows cost nothing."""
        fixture = _Fixture()

        for sql in (_FACTS_SQL, _TRAITS_SQL, _OBSERVATIONS_SQL):
            plan = fixture.conn.execute(
                "EXPLAIN QUERY PLAN " + sql.format(levels=":level0, :level1"),
                {"person_id": "P", "limit": 6, "level0": "public", "level1": "personal"},
            ).fetchall()
            steps = [str(step["detail"]) for step in plan]

            assert any(step.startswith("SEARCH") and "person" in step for step in steps), steps
            # The provenance subquery is indexed too, so it does not turn into a per-row scan.
            assert not any(step.startswith("SCAN") for step in steps), steps

    def test_a_dense_history_still_answers_with_one_page(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        for index in range(200):
            fixture.fact(alice.id, predicate=f"p{index}")

        assert len(fixture.reader.list_consolidation_facts(alice.id, limit=5, sensitivities=ORDINARY)) == 6


class TestOrdering:
    """Newest first, with the id breaking an exact tie."""

    def test_facts_are_placed_by_valid_from_when_they_assert_one(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        older = fixture.fact(alice.id, predicate="a", valid_from=date(2020, 1, 1))
        newer = fixture.fact(alice.id, predicate="b", valid_from=date(2026, 1, 1))

        rows = fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY)

        assert [row.fact_id for row in rows] == [newer.id, older.id]

    def test_an_undated_fact_is_placed_by_the_time_it_was_recorded(self) -> None:
        """The same placement the timeline uses, so one limit describes one window on both."""
        fixture = _Fixture()
        alice = fixture.person("Alice")
        undated = fixture.fact(alice.id, predicate="a")
        old_dated = fixture.fact(alice.id, predicate="b", valid_from=date(1990, 1, 1))

        rows = fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY)

        assert [row.fact_id for row in rows] == [undated.id, old_dated.id]

    def test_ordering_compares_instants_rather_than_stored_offsets(self) -> None:
        """`2026-06-01T23:30:00-05:00` is later than `2026-06-02T02:00:00+00:00` while sorting first."""
        fixture = _Fixture()
        alice = fixture.person("Alice")
        later = fixture.observation(
            alice.id,
            text="later",
            observed_at=datetime(2026, 6, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
        )
        earlier = fixture.observation(alice.id, text="earlier", observed_at=datetime(2026, 6, 2, 2, 0, tzinfo=UTC))

        rows = fixture.reader.list_consolidation_observations(alice.id, limit=10, sensitivities=ORDINARY)

        assert [row.observation_id for row in rows] == [later.id, earlier.id]

    def test_an_exact_tie_is_broken_by_ascending_id(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        instant = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        ids = sorted(
            fixture.observation(alice.id, text=f"note {index}", observed_at=instant).id for index in range(3)
        )

        rows = fixture.reader.list_consolidation_observations(alice.id, limit=10, sensitivities=ORDINARY)

        assert [row.observation_id for row in rows] == ids


class TestDisclosure:
    """The level filter is in SQL, before the page is cut."""

    def test_an_ordinary_read_excludes_elevated_records_of_every_type(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        ordinary_fact = fixture.fact(alice.id, predicate="a")
        fixture.fact(alice.id, predicate="b", sensitivity=Sensitivity.RESTRICTED)
        fixture.trait(alice.id, sensitivity=Sensitivity.SENSITIVE)
        fixture.observation(alice.id, sensitivity=Sensitivity.RESTRICTED)

        facts = fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY)

        assert [row.fact_id for row in facts] == [ordinary_fact.id]
        assert fixture.reader.list_consolidation_traits(alice.id, limit=10, sensitivities=ORDINARY) == []
        assert fixture.reader.list_consolidation_observations(alice.id, limit=10, sensitivities=ORDINARY) == []

    def test_an_elevated_record_does_not_displace_an_ordinary_one_from_the_page(self) -> None:
        """Filtering after the cut would return a short page that hid what was withheld."""
        fixture = _Fixture()
        alice = fixture.person("Alice")
        for index in range(3):
            fixture.fact(alice.id, predicate=f"hidden{index}", sensitivity=Sensitivity.RESTRICTED)
        visible = fixture.fact(alice.id, predicate="visible")

        rows = fixture.reader.list_consolidation_facts(alice.id, limit=1, sensitivities=ORDINARY)

        assert [row.fact_id for row in rows] == [visible.id]

    def test_the_widened_read_returns_every_level(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        fixture.fact(alice.id, predicate="a")
        fixture.fact(alice.id, predicate="b", sensitivity=Sensitivity.RESTRICTED)

        assert len(fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=EVERY_LEVEL)) == 2

    def test_a_caller_allowed_no_level_is_shown_nothing(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        fixture.fact(alice.id)

        assert fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=()) == []


class TestProvenanceAndEvidence:
    """M18 receipts and M18.3 citations travel with the rows that carry them."""

    def test_a_record_names_the_earliest_import_that_committed_onto_it(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        stored = fixture.fact(alice.id)
        fixture.add_source("S-late", "2026-05-01T00:00:00+00:00")
        fixture.add_source("S-early", "2026-01-01T00:00:00+00:00")
        fixture.map_candidate("C2", "S-late", "fact", stored.id, "2026-05-01T00:00:00+00:00")
        fixture.map_candidate("C1", "S-early", "fact", stored.id, "2026-01-01T00:00:00+00:00")

        rows = fixture.reader.list_consolidation_facts(alice.id, limit=10, sensitivities=ORDINARY)

        # Two mappings onto one entity stay one row naming the earliest, never two rows.
        assert [(row.fact_id, row.source_session_id) for row in rows] == [(stored.id, "S-early")]

    def test_trait_citations_follow_the_timelines_own_contract(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        bob = fixture.person("Bob")
        observation = fixture.observation(alice.id)
        interaction = fixture.interactions.execute(
            RecordInteractionInput(
                summary="review",
                participant_ids=[alice.id, bob.id],
                occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        trait = fixture.trait(alice.id, evidence_ids=[observation.id, interaction.id])

        links = fixture.reader.list_trait_evidence(trait.id, limit=10, sensitivities=ORDINARY)

        assert {(link.evidence_type, link.evidence_id) for link in links} == {
            (ENTRY_OBSERVATION, observation.id),
            (ENTRY_INTERACTION, interaction.id),
        }

    def test_a_citation_whose_record_is_elevated_is_withheld(self) -> None:
        fixture = _Fixture()
        alice = fixture.person("Alice")
        restricted = fixture.observation(alice.id, sensitivity=Sensitivity.RESTRICTED)
        trait = fixture.trait(alice.id, evidence_ids=[restricted.id])

        assert fixture.reader.list_trait_evidence(trait.id, limit=10, sensitivities=ORDINARY) == []


def test_the_read_writes_no_audit_or_changelog_row() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.fact(alice.id)
    fixture.trait(alice.id)
    fixture.observation(alice.id)
    before = fixture.counts()

    result = GetConsolidationContext(fixture.people, fixture.reader).execute(alice.id)

    assert result.found is True
    assert fixture.counts() == before


def test_the_use_case_reads_end_to_end_through_the_real_reader() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.fact(alice.id, predicate="employer", value="Acme", valid_from=date(2024, 1, 1))
    fixture.fact(alice.id, predicate="employer", value="Globex", valid_from=date(2026, 7, 1))

    result = GetConsolidationContext(fixture.people, fixture.reader).execute(alice.id)

    assert [signal.kind for signal in result.signals] == ["contradictory_fact"]
    assert result.signals[0].key == "employer"
