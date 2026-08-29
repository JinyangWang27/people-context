"""Real-SQLite projection behaviour for the bounded person timeline."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePersonTimelineReader,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.adapters.sqlite.timeline_reader import _BRANCHES, _TIMELINE_SQL
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.insights import MAX_TIMELINE_EVIDENCE_LINKS, GetPersonTimeline
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
    RecordTrait,
    RecordTraitInput,
    SetAffiliation,
    SetAffiliationInput,
)
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory
from people_context.ports.clock import SystemClock
from people_context.ports.timeline import (
    BASIS_CREATED_AT,
    BASIS_OCCURRED_AT,
    BASIS_RECORDED_AT,
    BASIS_UPDATED_AT,
    BASIS_VALID_FROM,
    ENTRY_AFFILIATION,
    ENTRY_FACT,
    ENTRY_INTERACTION,
    ENTRY_OBSERVATION,
    ENTRY_RELATIONSHIP,
    ENTRY_TRAIT,
    TIMELINE_BASES,
    TIMELINE_ENTRY_TYPES,
    PersonTimelineReader,
    TimelineRow,
)

ORDINARY = (Sensitivity.PUBLIC, Sensitivity.PERSONAL)
EVERY_LEVEL = (Sensitivity.PUBLIC, Sensitivity.PERSONAL, Sensitivity.SENSITIVE, Sensitivity.RESTRICTED)


class _Fixture:
    """A live SQLite database with the writers needed to seed one person's history."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection = open_db(":memory:")
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()
        self.organizations = SqliteOrganizationStore(self.conn)
        self.trait_evidence = SqliteTraitEvidenceStore(self.conn)
        self.reader = SqlitePersonTimelineReader(self.conn)
        self.interactions = RecordInteraction(self.people, self.records, self.audit, self.clock)
        self.observations = RecordObservation(self.people, self.records, self.audit, self.clock)
        self.facts = RecordFact(self.people, self.records, self.audit, self.clock)
        self.traits = RecordTrait(
            self.people, self.records, self.audit, self.clock, self.trait_evidence
        )
        self.affiliations = SetAffiliation(
            self.people, self.organizations, self.records, self.audit, self.clock
        )
        self.relationships = SetRelationship(
            self.people,
            SqliteRelationshipStore(self.conn),
            self.audit,
            self.clock,
            SqliteRelationshipVocabularyStore(self.conn),
        )

    def person(self, name: str, *, is_self: bool = False) -> Person:
        person = Person(canonical_name=name, is_self=is_self)
        self.people.save_person(person)
        return person

    def rows(self, person_id: str, *, limit: int = 50, levels: tuple[Sensitivity, ...] = ORDINARY) -> list[TimelineRow]:
        return self.reader.list_timeline_rows(person_id, limit=limit, sensitivities=levels)

    def counts(self) -> tuple[int, int]:
        audit = self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        changelog = self.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0]
        return audit, changelog

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


def test_the_sqlite_reader_satisfies_the_declared_port() -> None:
    assert isinstance(SqlitePersonTimelineReader(open_db(":memory:")), PersonTimelineReader)


def test_every_record_type_reaches_the_timeline_with_its_own_basis() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.interactions.execute(
        RecordInteractionInput(
            summary="Coffee",
            participant_ids=[alice.id, bob.id],
            occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
            channel="in person",
        )
    )
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="Prefers async updates",
            observed_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
    )
    fixture.facts.execute(
        RecordFactInput(person_id=alice.id, predicate="city", value="Berlin", valid_from=date(2026, 3, 1))
    )
    fixture.affiliations.execute(
        SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer", valid_from=date(2026, 2, 1))
    )
    fixture.relationships.execute(
        SetRelationshipInput(subject_id=alice.id, object_id=bob.id, type="colleague of")
    )
    fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.COMMUNICATION_STYLE, value="direct")
    )

    rows = {row.entry_type: row for row in fixture.rows(alice.id)}

    assert set(rows) == {
        ENTRY_INTERACTION,
        ENTRY_OBSERVATION,
        ENTRY_FACT,
        ENTRY_AFFILIATION,
        ENTRY_RELATIONSHIP,
        ENTRY_TRAIT,
    }
    assert (rows[ENTRY_INTERACTION].summary, rows[ENTRY_INTERACTION].detail) == ("Coffee", "in person")
    assert rows[ENTRY_INTERACTION].basis == BASIS_OCCURRED_AT
    assert rows[ENTRY_OBSERVATION].summary == "Prefers async updates"
    assert (rows[ENTRY_FACT].summary, rows[ENTRY_FACT].detail) == ("city", "Berlin")
    assert (rows[ENTRY_AFFILIATION].summary, rows[ENTRY_AFFILIATION].detail) == ("Engineer", "Acme")
    assert (rows[ENTRY_RELATIONSHIP].summary, rows[ENTRY_RELATIONSHIP].detail) == ("colleague_of", "Bob")
    assert (rows[ENTRY_TRAIT].summary, rows[ENTRY_TRAIT].detail) == ("communication_style", "direct")
    assert rows[ENTRY_TRAIT].basis == BASIS_UPDATED_AT


def test_a_dated_record_is_placed_at_its_validity_start_and_keeps_the_date() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.facts.execute(
        RecordFactInput(
            person_id=alice.id,
            predicate="employer",
            value="Acme",
            valid_from=date(2026, 3, 15),
            valid_to=date(2026, 12, 31),
        )
    )

    row = fixture.rows(alice.id)[0]

    assert row.basis == BASIS_VALID_FROM
    assert row.effective_at == datetime(2026, 3, 15, tzinfo=UTC)
    assert (row.valid_from, row.valid_to) == (date(2026, 3, 15), date(2026, 12, 31))


def test_an_undated_record_is_placed_at_its_recording_time_and_says_so() -> None:
    """Nothing is dropped for lacking a validity start, and no timestamp is invented for it."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.facts.execute(RecordFactInput(person_id=alice.id, predicate="pronouns", value="they/them"))
    fixture.affiliations.execute(SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer"))

    rows = {row.entry_type: row for row in fixture.rows(alice.id)}

    assert rows[ENTRY_FACT].basis == BASIS_RECORDED_AT
    assert rows[ENTRY_FACT].valid_from is None
    assert rows[ENTRY_AFFILIATION].basis == BASIS_CREATED_AT


def test_elevated_records_are_filtered_in_sql_rather_than_after_the_page_is_cut() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    for level in (Sensitivity.PUBLIC, Sensitivity.PERSONAL, Sensitivity.SENSITIVE, Sensitivity.RESTRICTED):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"{level.value} note",
                observed_at=datetime(2026, 5, 1, tzinfo=UTC),
                sensitivity=level,
            )
        )

    ordinary = fixture.rows(alice.id, levels=ORDINARY)
    everything = fixture.rows(alice.id, levels=EVERY_LEVEL)

    assert sorted(row.sensitivity for row in ordinary if row.sensitivity is not None) == sorted(ORDINARY)
    assert len(everything) == 4


def test_an_ordinary_page_is_full_even_when_elevated_records_are_newer() -> None:
    """Filtering after the cut would have returned a short page and hidden that anything was."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    for day in range(1, 4):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text="restricted",
                observed_at=datetime(2026, 6, day, tzinfo=UTC),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
    for day in range(1, 4):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text="ordinary",
                observed_at=datetime(2026, 5, day, tzinfo=UTC),
            )
        )

    rows = fixture.rows(alice.id, limit=2, levels=ORDINARY)

    # Two pages' worth of ordinary rows exist, so the reader returns the page plus its one probe.
    assert len(rows) == 3
    assert {row.summary for row in rows} == {"ordinary"}


def test_the_reader_returns_exactly_one_row_past_the_requested_page() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    for day in range(1, 11):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"day {day}",
                observed_at=datetime(2026, 5, day, tzinfo=UTC),
            )
        )

    assert len(fixture.rows(alice.id, limit=3)) == 4
    assert len(fixture.rows(alice.id, limit=10)) == 10


def test_sqlite_orders_by_the_normalized_instant_not_the_stored_text() -> None:
    """`2026-06-01T23:30:00-05:00` is the later instant despite sorting first as text."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="later",
            observed_at=datetime(2026, 6, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
        )
    )
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="earlier",
            observed_at=datetime(2026, 6, 2, 2, 0, tzinfo=UTC),
        )
    )

    assert [row.summary for row in fixture.rows(alice.id)] == ["later", "earlier"]


def test_a_naive_stored_timestamp_is_normalized_as_utc_by_sqlite_too() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="naive", observed_at=datetime(2026, 6, 1, 12, 0))
    )
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id, text="aware", observed_at=datetime(2026, 6, 1, 13, 0, tzinfo=UTC)
        )
    )

    assert [row.summary for row in fixture.rows(alice.id)] == ["aware", "naive"]


def test_the_page_is_the_newest_rows_across_every_type() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="old", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    fixture.interactions.execute(
        RecordInteractionInput(
            summary="newest",
            participant_ids=[alice.id],
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )
    fixture.facts.execute(
        RecordFactInput(person_id=alice.id, predicate="city", value="Berlin", valid_from=date(2026, 5, 1))
    )

    assert [row.summary for row in fixture.rows(alice.id, limit=2)][:2] == ["newest", "city"]


def test_an_interaction_a_person_attended_appears_once_however_many_participants_it_had() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    others = [fixture.person(name) for name in ("Bob", "Carol", "Dan")]
    fixture.interactions.execute(
        RecordInteractionInput(
            summary="Team lunch",
            participant_ids=[alice.id, *(person.id for person in others)],
            occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )

    rows = fixture.rows(alice.id)

    assert [row.entry_type for row in rows] == [ENTRY_INTERACTION]


def test_another_persons_records_never_reach_this_timeline() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.observations.execute(
        RecordObservationInput(person_id=bob.id, text="about bob", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )
    fixture.interactions.execute(
        RecordInteractionInput(
            summary="bob only",
            participant_ids=[bob.id],
            occurred_at=datetime(2026, 5, 2, tzinfo=UTC),
        )
    )

    assert fixture.rows(alice.id) == []


def test_a_relationship_is_reported_from_either_endpoint_and_names_the_counterpart() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.relationships.execute(
        SetRelationshipInput(subject_id=bob.id, object_id=alice.id, type="colleague of")
    )

    alice_row = fixture.rows(alice.id)[0]
    bob_row = fixture.rows(bob.id)[0]

    assert alice_row.detail == "Bob"
    assert bob_row.detail == "Alice"
    assert alice_row.entry_id == bob_row.entry_id


def test_an_asymmetric_edge_reads_from_each_persons_own_side() -> None:
    """A stored `parent_of` shown unchanged on the child's timeline would state it backwards."""
    fixture = _Fixture()
    parent = fixture.person("Parent")
    child = fixture.person("Child")
    fixture.relationships.execute(
        SetRelationshipInput(subject_id=parent.id, object_id=child.id, type="parent of")
    )

    assert fixture.rows(parent.id)[0].summary == "parent_of"
    assert fixture.rows(child.id)[0].summary == "child_of"


def test_an_edge_to_a_removed_person_is_omitted_rather_than_reappearing_here() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    ghost = fixture.person("Ghost")
    fixture.relationships.execute(
        SetRelationshipInput(subject_id=alice.id, object_id=ghost.id, type="colleague of")
    )
    fixture.people.save_person(ghost.model_copy(update={"deleted_at": datetime(2026, 1, 1, tzinfo=UTC)}))

    assert fixture.rows(alice.id) == []


def test_provenance_names_the_import_that_produced_a_record() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    observation = fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="imported", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )
    fixture.add_source("S1", "2026-05-01T00:00:00+00:00")
    fixture.map_candidate("C1", "S1", ENTRY_OBSERVATION, observation.id, "2026-05-01T00:00:00+00:00")

    assert fixture.rows(alice.id)[0].source_session_id == "S1"


def test_a_record_reused_by_a_later_import_stays_one_entry_naming_the_first_source() -> None:
    """A join would have multiplied the record into one entry per import that touched it."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    observation = fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="reused", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )
    fixture.add_source("S-later", "2026-06-01T00:00:00+00:00")
    fixture.add_source("S-first", "2026-05-01T00:00:00+00:00")
    fixture.map_candidate("C2", "S-later", ENTRY_OBSERVATION, observation.id, "2026-06-01T00:00:00+00:00")
    fixture.map_candidate("C1", "S-first", ENTRY_OBSERVATION, observation.id, "2026-05-01T00:00:00+00:00")

    rows = fixture.rows(alice.id)

    assert len(rows) == 1
    assert rows[0].source_session_id == "S-first"


def test_a_record_no_import_produced_carries_no_source() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="by hand", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )

    assert fixture.rows(alice.id)[0].source_session_id is None


def test_trait_evidence_carries_the_cited_records_own_level() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    ordinary = fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="ordinary", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )
    restricted = fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="restricted",
            observed_at=datetime(2026, 5, 2, tzinfo=UTC),
            sensitivity=Sensitivity.RESTRICTED,
        )
    )
    interaction = fixture.interactions.execute(
        RecordInteractionInput(
            summary="call",
            participant_ids=[alice.id],
            occurred_at=datetime(2026, 5, 3, tzinfo=UTC),
            sensitivity=Sensitivity.SENSITIVE,
        )
    )
    trait = fixture.traits.execute(
        RecordTraitInput(
            person_id=alice.id,
            category=TraitCategory.COMMUNICATION_STYLE,
            value="direct",
            evidence_ids=[ordinary.id, restricted.id, interaction.id],
        )
    )

    ordinary_links = fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=ORDINARY)
    every_link = fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=EVERY_LEVEL)

    # The cited record's own level decides, and the filter is in the read rather than after it.
    assert [link.evidence_id for link in ordinary_links] == [ordinary.id]
    assert {link.evidence_id for link in every_link} == {ordinary.id, restricted.id, interaction.id}


def test_trait_evidence_is_ordered_stably_and_reads_one_row_past_the_limit() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    observations = [
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"note {index}",
                observed_at=datetime(2026, 5, 1, tzinfo=UTC),
            )
        )
        for index in range(4)
    ]
    trait = fixture.traits.execute(
        RecordTraitInput(
            person_id=alice.id,
            category=TraitCategory.COMMUNICATION_STYLE,
            value="direct",
            evidence_ids=[observation.id for observation in observations],
        )
    )

    page = fixture.reader.list_trait_evidence(trait.id, limit=2, sensitivities=ORDINARY)

    assert len(page) == 3
    assert [link.evidence_id for link in page] == sorted(link.evidence_id for link in page)


def test_a_trait_without_evidence_reads_no_links() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    trait = fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.VALUES, value="candour")
    )

    assert fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=ORDINARY) == []


def test_reading_the_timeline_writes_nothing() -> None:
    """A projection is a read: no audit row, no changelog row, no durable state."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="note", observed_at=datetime(2026, 5, 1, tzinfo=UTC))
    )
    trait = fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.VALUES, value="candour")
    )
    before = fixture.counts()

    GetPersonTimeline(fixture.people, fixture.reader).execute(alice.id, include_sensitive=True)
    fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=EVERY_LEVEL)

    assert fixture.counts() == before


def test_every_branch_seeks_by_person_rather_than_scanning_a_record_table() -> None:
    """A timeline read must cost what one person holds, not what the database holds."""
    fixture = _Fixture()
    plan = fixture.conn.execute(
        "EXPLAIN QUERY PLAN " + _TIMELINE_SQL.format(levels=":level0, :level1"),
        {"person_id": "P", "limit": 51, "level0": "public", "level1": "personal"},
    ).fetchall()
    steps = [row["detail"] for row in plan]

    for index in (
        "idx_interaction_participants_person",
        "idx_observations_person",
        "idx_facts_person",
        "idx_affiliations_person",
        "idx_relationships_subject",
        "idx_relationships_object",
        "idx_traits_person",
        "idx_import_candidate_mappings_entity",
    ):
        assert any(index in step for step in steps), (index, steps)
    # The only scan is over the assembled union itself; no record table is read end to end. The
    # subquery's number is SQLite's own bookkeeping and varies by version, so it is not asserted.
    scans = [step for step in steps if step.startswith("SCAN")]
    assert scans and all(step.startswith("SCAN (subquery") for step in scans), scans


def test_a_dense_history_still_returns_one_page() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    other = fixture.person("Bob")
    for index in range(300):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"note {index}",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
        )
        # A second person's records must not enlarge the first person's read either.
        fixture.observations.execute(
            RecordObservationInput(
                person_id=other.id,
                text=f"other {index}",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
        )

    rows = fixture.rows(alice.id, limit=25)

    assert len(rows) == 26
    assert rows[0].summary == "note 299"
    assert all(row.summary.startswith("note ") for row in rows)


def test_the_projection_only_emits_the_declared_vocabulary() -> None:
    """`entry_type` and `basis` are the document's contract, so nothing may emit a value outside it."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.interactions.execute(
        RecordInteractionInput(
            summary="Coffee",
            participant_ids=[alice.id, bob.id],
            occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    fixture.observations.execute(
        RecordObservationInput(person_id=alice.id, text="note", observed_at=datetime(2026, 4, 1, tzinfo=UTC))
    )
    fixture.facts.execute(RecordFactInput(person_id=alice.id, predicate="pronouns", value="they/them"))
    fixture.facts.execute(
        RecordFactInput(person_id=alice.id, predicate="city", value="Berlin", valid_from=date(2026, 1, 1))
    )
    fixture.affiliations.execute(SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer"))
    fixture.relationships.execute(
        SetRelationshipInput(
            subject_id=alice.id,
            object_id=bob.id,
            type="colleague of",
            valid_from=date(2025, 1, 1),
        )
    )
    fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.VALUES, value="candour")
    )

    rows = fixture.rows(alice.id)

    assert {row.entry_type for row in rows} <= set(TIMELINE_ENTRY_TYPES)
    assert {row.basis for row in rows} <= set(TIMELINE_BASES)
    # Every declared type and both dating rules are exercised by this fixture.
    assert {row.entry_type for row in rows} == set(TIMELINE_ENTRY_TYPES)
    assert {BASIS_VALID_FROM, BASIS_RECORDED_AT, BASIS_CREATED_AT} <= {row.basis for row in rows}


def test_a_sub_millisecond_newer_record_is_not_dropped_at_the_page_boundary() -> None:
    """The ordering key is exact to the microsecond, not to SQLite's millisecond `%f`.

    Three observations inside one millisecond share a millisecond-resolution key. Ordering by that
    key alone leaves the tie to `entry_id`, and ULIDs ascend with creation time, so a `limit=1` page
    would keep the two *oldest* rows and drop the newest one entirely.
    """
    fixture = _Fixture()
    alice = fixture.person("Alice")
    for microsecond in (100, 200, 400):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"micro {microsecond}",
                observed_at=datetime(2026, 5, 1, 12, 0, 0, microsecond, tzinfo=UTC),
            )
        )

    page = fixture.rows(alice.id, limit=1)

    assert page[0].summary == "micro 400"
    assert [row.summary for row in fixture.rows(alice.id)] == ["micro 400", "micro 200", "micro 100"]


def test_sub_millisecond_order_holds_across_stored_offsets() -> None:
    """The fraction needs no conversion: every real offset is a whole number of minutes."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="later",
            observed_at=datetime(2026, 6, 1, 7, 0, 0, 400, tzinfo=timezone(timedelta(hours=-5))),
        )
    )
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="earlier",
            observed_at=datetime(2026, 6, 1, 12, 0, 0, 100, tzinfo=UTC),
        )
    )

    # Both are 12:00:00 UTC to the second; only the microseconds separate them.
    assert [row.summary for row in fixture.rows(alice.id, limit=1)][:1] == ["later"]


def test_a_record_with_no_stored_fraction_orders_before_one_in_the_same_second() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="whole second",
            observed_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
    )
    fixture.observations.execute(
        RecordObservationInput(
            person_id=alice.id,
            text="one microsecond later",
            observed_at=datetime(2026, 5, 1, 12, 0, 0, 1, tzinfo=UTC),
        )
    )

    assert [row.summary for row in fixture.rows(alice.id)] == ["one microsecond later", "whole second"]


def test_every_branch_is_cut_to_one_page_before_the_union() -> None:
    """A `LIMIT` on the compound alone would still sort a whole history to answer `--limit 1`.

    The structural assertion is the point: the bound has to be inside each branch, so that no read
    materializes or sorts more than one page per record type before the union is assembled.
    """
    assert _TIMELINE_SQL.count("LIMIT :limit") == len(_BRANCHES) + 1


def test_a_dense_single_branch_history_still_returns_only_a_page() -> None:
    """The per-branch cut is what keeps a large history from being sorted whole."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    for index in range(400):
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"note {index}",
                observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            )
        )

    rows = fixture.rows(alice.id, limit=5)

    assert len(rows) == 6
    assert [row.summary for row in rows][:3] == ["note 399", "note 398", "note 397"]


def test_a_citation_keeps_the_type_that_makes_its_id_resolvable() -> None:
    """A restored store may hold an observation and an interaction under one id; both are citable."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    shared = "shared-evidence-id"
    fixture.conn.execute(
        "INSERT INTO observations (id, person_id, text, observed_at, sensitivity, provenance_source) "
        "VALUES (?, ?, ?, ?, 'personal', 'agent')",
        (shared, alice.id, "an observation", "2026-05-01T00:00:00+00:00"),
    )
    fixture.conn.execute(
        "INSERT INTO interactions (id, summary, occurred_at, sensitivity, provenance_source) "
        "VALUES (?, ?, ?, 'personal', 'agent')",
        (shared, "an interaction", "2026-05-02T00:00:00+00:00"),
    )
    trait = fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.VALUES, value="candour")
    )
    for evidence_type in ("observation", "interaction"):
        fixture.conn.execute(
            "INSERT INTO trait_evidence (trait_id, evidence_type, evidence_id, created_at) "
            "VALUES (?, ?, ?, '2026-05-03T00:00:00+00:00')",
            (trait.id, evidence_type, shared),
        )

    links = fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=ORDINARY)

    assert [(link.evidence_type, link.evidence_id) for link in links] == [
        ("interaction", shared),
        ("observation", shared),
    ]


def test_a_citation_whose_record_is_gone_is_excluded_rather_than_named() -> None:
    """A dangling link matches no level, so it fails closed instead of naming an unaccounted id."""
    fixture = _Fixture()
    alice = fixture.person("Alice")
    trait = fixture.traits.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.VALUES, value="candour")
    )
    fixture.conn.execute(
        "INSERT INTO trait_evidence (trait_id, evidence_type, evidence_id, created_at) "
        "VALUES (?, 'observation', 'no-such-observation', '2026-05-03T00:00:00+00:00')",
        (trait.id,),
    )

    assert fixture.reader.list_trait_evidence(trait.id, limit=32, sensitivities=EVERY_LEVEL) == []


def test_an_ordinary_read_of_wholly_elevated_evidence_reads_nothing_at_all() -> None:
    """The count a truncation flag is derived from must not include links the caller cannot read.

    A trait carrying more elevated links than one page reports would otherwise answer an ordinary
    caller with no citations *and* a truncation flag, which together prove the hidden links exist.
    """
    fixture = _Fixture()
    alice = fixture.person("Alice")
    hidden = [
        fixture.observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text=f"restricted {index}",
                observed_at=datetime(2026, 5, 1, tzinfo=UTC),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        for index in range(MAX_TIMELINE_EVIDENCE_LINKS + 1)
    ]
    trait = fixture.traits.execute(
        RecordTraitInput(
            person_id=alice.id,
            category=TraitCategory.VALUES,
            value="candour",
            sensitivity=Sensitivity.PERSONAL,
        )
    )
    for observation in hidden:
        fixture.conn.execute(
            "INSERT INTO trait_evidence (trait_id, evidence_type, evidence_id, created_at) "
            "VALUES (?, 'observation', ?, '2026-05-03T00:00:00+00:00')",
            (trait.id, observation.id),
        )

    result = GetPersonTimeline(fixture.people, fixture.reader).execute(alice.id)
    entry = next(item for item in result.entries if item.entry_type == ENTRY_TRAIT)

    assert entry.evidence == []
    assert entry.evidence_truncated is False
