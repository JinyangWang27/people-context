"""Real-SQLite aggregation behaviour for the recency insight port."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecencyReader,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.adapters.sqlite.insights_reader import _INTERACTIONS_SQL, ORDINARY_SENSITIVITIES
from people_context.app.records import RecordInteraction, RecordInteractionInput
from people_context.app.relationships import (
    AddRelationshipType,
    AddRelationshipTypeInput,
    SetRelationship,
    SetRelationshipInput,
)
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock
from people_context.ports.insights import RecencyReader

AS_OF = date(2026, 6, 1)


class _Fixture:
    """A live SQLite database with the writers needed to seed recency signal."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection = open_db(":memory:")
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()
        self.vocabulary = SqliteRelationshipVocabularyStore(self.conn)
        self.relationships = SetRelationship(
            self.people,
            SqliteRelationshipStore(self.conn),
            self.audit,
            self.clock,
            self.vocabulary,
        )
        self.interactions = RecordInteraction(self.people, self.records, self.audit, self.clock)
        self.reader: RecencyReader = SqliteRecencyReader(self.conn)

    def person(self, name: str, *, is_self: bool = False) -> Person:
        person = Person(canonical_name=name, is_self=is_self)
        self.people.save_person(person)
        return person

    def interaction(
        self,
        person: Person,
        occurred_at: datetime,
        *,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> None:
        self.interactions.execute(
            RecordInteractionInput(
                summary="caught up",
                participant_ids=[person.id],
                occurred_at=occurred_at,
                sensitivity=sensitivity,
            )
        )

    def signals(self, *, category: str | None = None):
        return {
            signal.person_id: signal for signal in self.reader.list_recency_signals(as_of=AS_OF, category=category)
        }


def test_only_ordinary_interactions_contribute_to_recency() -> None:
    fixture = _Fixture()
    ordinary = fixture.person("Ordinary")
    elevated = fixture.person("Elevated")
    untouched = fixture.person("Untouched")
    fixture.interaction(ordinary, datetime(2026, 1, 5, tzinfo=UTC), sensitivity=Sensitivity.PUBLIC)
    fixture.interaction(ordinary, datetime(2026, 2, 5, tzinfo=UTC), sensitivity=Sensitivity.PERSONAL)
    fixture.interaction(ordinary, datetime(2026, 5, 5, tzinfo=UTC), sensitivity=Sensitivity.SENSITIVE)
    fixture.interaction(elevated, datetime(2026, 5, 6, tzinfo=UTC), sensitivity=Sensitivity.SENSITIVE)
    fixture.interaction(elevated, datetime(2026, 5, 7, tzinfo=UTC), sensitivity=Sensitivity.RESTRICTED)

    signals = fixture.signals()

    assert signals[ordinary.id].last_interaction_at == datetime(2026, 2, 5, tzinfo=UTC)
    assert signals[ordinary.id].interaction_count == 2
    # A person with only elevated interactions is indistinguishable from one with none.
    assert signals[elevated.id].last_interaction_at is None
    assert signals[elevated.id].interaction_count == 0
    assert signals[untouched.id].last_interaction_at is None
    assert signals[untouched.id].interaction_count == 0


def test_sensitivity_filtering_happens_in_sql() -> None:
    fixture = _Fixture()
    elevated_only = fixture.person("Elevated only")
    ordinary = fixture.person("Ordinary")
    fixture.interaction(elevated_only, datetime(2026, 5, 5, tzinfo=UTC), sensitivity=Sensitivity.RESTRICTED)
    fixture.interaction(ordinary, datetime(2026, 5, 6, tzinfo=UTC), sensitivity=Sensitivity.PERSONAL)

    # Execute the adapter's own statement directly: the elevated row must already be
    # absent from what SQLite returns, never filtered afterwards in Python.
    rows = [
        (row["person_id"], row["occurred_at"])
        for row in fixture.conn.execute(_INTERACTIONS_SQL, ORDINARY_SENSITIVITIES).fetchall()
    ]

    assert rows == [(ordinary.id, "2026-05-06T00:00:00+00:00")]
    assert elevated_only.id not in {person_id for person_id, _ in rows}


def test_the_latest_interaction_is_chosen_by_instant_not_by_stored_text() -> None:
    fixture = _Fixture()
    person = fixture.person("Mixed offsets")
    # 23:30-05:00 is 04:30Z the next day, so it is the later instant even though its
    # stored text sorts before the +00:00 row.
    earlier_instant = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    later_instant = datetime(2026, 6, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    fixture.interaction(person, earlier_instant)
    fixture.interaction(person, later_instant)

    signal = fixture.signals()[person.id]

    assert signal.last_interaction_at == later_instant
    assert signal.last_interaction_at.utcoffset() == timedelta(hours=-5)
    assert signal.interaction_count == 2


def test_sub_millisecond_ties_are_resolved_by_instant_not_by_creation_order() -> None:
    fixture = _Fixture()
    person = fixture.person("Same millisecond")
    # Both round to ...123Z, so any millisecond-resolution key ties. The later
    # microsecond is recorded second, giving it the larger id, so ordering by id would
    # be right here by accident; the next test removes that coincidence.
    later_microsecond = datetime(2026, 5, 6, 12, 0, 0, 123499, tzinfo=UTC)
    earlier_microsecond = datetime(2026, 5, 6, 12, 0, 0, 123400, tzinfo=UTC)
    fixture.interaction(person, earlier_microsecond)
    fixture.interaction(person, later_microsecond)

    assert fixture.signals()[person.id].last_interaction_at == later_microsecond


def test_a_sub_millisecond_tie_across_offsets_picks_the_later_instant_and_its_date() -> None:
    fixture = _Fixture()
    person = fixture.person("Mixed offsets in one millisecond")
    # Same millisecond, different offsets, and — crucially — different stored calendar
    # dates. Text ordering picks the +00:00 row because "2026-06-02" > "2026-06-01",
    # but the -05:00 row is the later instant by 99 microseconds. Since the age is
    # measured from the selected timestamp's own date, picking wrong moves days_since.
    earlier_instant_later_date = datetime(2026, 6, 2, 0, 0, 0, 123400, tzinfo=UTC)
    later_instant_earlier_date = datetime(
        2026, 6, 1, 19, 0, 0, 123499, tzinfo=timezone(timedelta(hours=-5))
    )
    fixture.interaction(person, earlier_instant_later_date)
    fixture.interaction(person, later_instant_earlier_date)

    signal = fixture.signals()[person.id]

    assert signal.last_interaction_at == later_instant_earlier_date
    assert signal.last_interaction_at.date() == date(2026, 6, 1)
    assert signal.interaction_count == 2


def test_the_reported_timestamp_keeps_its_stored_precision_and_offset() -> None:
    fixture = _Fixture()
    person = fixture.person("Precise")
    occurred_at = datetime(2026, 5, 6, 12, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    fixture.interaction(person, occurred_at)

    assert fixture.signals()[person.id].last_interaction_at == occurred_at


def test_soft_deleted_and_self_people_are_not_reported() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    gone = fixture.person("Gone")
    fixture.conn.execute(
        "UPDATE persons SET deleted_at = ? WHERE id = ?",
        (datetime(2026, 4, 1, tzinfo=UTC).isoformat(), gone.id),
    )

    signals = fixture.signals()

    assert me.id not in signals
    assert gone.id not in signals


def test_active_relationship_to_self_categories_are_deduplicated_and_ordered() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    alice = fixture.person("Alice")
    stranger = fixture.person("Stranger")
    fixture.relationships.execute(SetRelationshipInput(subject_id=me.id, object_id=alice.id, type="friend of"))
    fixture.relationships.execute(SetRelationshipInput(subject_id=alice.id, object_id=me.id, type="neighbor of"))
    fixture.relationships.execute(SetRelationshipInput(subject_id=me.id, object_id=alice.id, type="reports to"))

    signals = fixture.signals()

    assert signals[alice.id].categories == ("professional", "social")
    assert signals[stranger.id].categories == ()


def test_expired_relationships_are_excluded_from_categories() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    alice = fixture.person("Alice")
    fixture.relationships.execute(
        SetRelationshipInput(
            subject_id=me.id,
            object_id=alice.id,
            type="colleague of",
            valid_to=date(2025, 1, 1),
        )
    )

    assert fixture.signals()[alice.id].categories == ()


def test_relationships_between_other_people_are_not_relationships_to_self() -> None:
    fixture = _Fixture()
    fixture.person("Me", is_self=True)
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.relationships.execute(SetRelationshipInput(subject_id=alice.id, object_id=bob.id, type="friend of"))

    signals = fixture.signals()

    assert signals[alice.id].categories == ()
    assert signals[bob.id].categories == ()


def test_custom_vocabulary_category_is_reported_and_filterable() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    AddRelationshipType(fixture.vocabulary, fixture.vocabulary, fixture.audit, fixture.clock).execute(
        AddRelationshipTypeInput(type="climbing_partner_of", category="hobby", symmetric=True)
    )
    fixture.relationships.execute(
        SetRelationshipInput(subject_id=me.id, object_id=alice.id, type="climbing_partner_of")
    )
    fixture.relationships.execute(SetRelationshipInput(subject_id=me.id, object_id=bob.id, type="friend of"))

    assert fixture.signals()[alice.id].categories == ("hobby",)
    assert set(fixture.signals(category="hobby")) == {alice.id}
    assert set(fixture.signals(category="social")) == {bob.id}


def test_uncategorized_relationship_types_report_a_stable_category() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    alice = fixture.person("Alice")
    fixture.conn.execute(
        "INSERT INTO relationships (id, subject_id, object_id, type, confidence, provenance_source, created_at) "
        "VALUES ('rel-legacy', ?, ?, 'pen_pal_of', 1.0, 'test', ?)",
        (me.id, alice.id, datetime(2026, 1, 1, tzinfo=UTC).isoformat()),
    )

    assert fixture.signals()[alice.id].categories == ("uncategorized",)


def test_without_a_self_person_no_categories_are_reported() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice")
    bob = fixture.person("Bob")
    fixture.relationships.execute(SetRelationshipInput(subject_id=alice.id, object_id=bob.id, type="friend of"))

    assert fixture.signals()[alice.id].categories == ()
    assert fixture.signals(category="social") == {}
