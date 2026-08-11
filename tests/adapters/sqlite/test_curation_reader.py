"""Real-SQLite evidence queries behind the data-quality report."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteCurationReader,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    SetAffiliation,
    SetAffiliationInput,
)
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock
from people_context.ports.curation import CurationReader


class _Fixture:
    """A live SQLite database with the writers needed to seed curation evidence."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection = open_db(":memory:")
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()
        self.reader = SqliteCurationReader(self.conn)

    def person(self, name: str, *, is_self: bool = False, aliases: list[Alias] | None = None) -> Person:
        person = Person(canonical_name=name, is_self=is_self, aliases=aliases or [])
        self.people.save_person(person)
        return person

    def soft_delete(self, person: Person) -> Person:
        """Mark a person soft-deleted, leaving every row that already points at them."""
        deleted = person.model_copy(update={"deleted_at": datetime.now(UTC)})
        self.people.save_person(deleted)
        return deleted

    def fact(
        self,
        person: Person,
        predicate: str,
        value: str,
        *,
        valid_from: date | None = None,
        valid_to: date | None = None,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> str:
        result = RecordFact(self.people, self.records, self.audit, self.clock).execute(
            RecordFactInput(
                person_id=person.id,
                predicate=predicate,
                value=value,
                valid_from=valid_from,
                valid_to=valid_to,
                sensitivity=sensitivity,
            )
        )
        return result.id

    def relationship(self, subject: Person, object_: Person, type_: str = "friend of") -> str:
        result = SetRelationship(
            self.people,
            SqliteRelationshipStore(self.conn),
            self.audit,
            self.clock,
            SqliteRelationshipVocabularyStore(self.conn),
        ).execute(SetRelationshipInput(subject_id=subject.id, object_id=object_.id, type=type_))
        return result.id

    def affiliation(self, person: Person, org: str = "Acme") -> str:
        result = SetAffiliation(
            self.people,
            SqliteOrganizationStore(self.conn),
            self.records,
            self.audit,
            self.clock,
        ).execute(SetAffiliationInput(person_id=person.id, org=org, role="Engineer"))
        return result.id

    def interaction(self, *participants: Person, summary: str = "quarterly sync") -> str:
        result = RecordInteraction(self.people, self.records, self.audit, self.clock).execute(
            RecordInteractionInput(
                summary=summary,
                participant_ids=[person.id for person in participants],
                occurred_at=datetime.now(UTC),
            )
        )
        return result.id


def _handle(value: str) -> Alias:
    return Alias(value=value, kind=AliasKind.HANDLE)


def test_the_sqlite_reader_satisfies_the_curation_port() -> None:
    assert isinstance(SqliteCurationReader(open_db(":memory:")), CurationReader)


def test_a_clean_store_yields_no_candidate_evidence() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang", aliases=[_handle("azhang"), Alias(value="Ali")])
    fixture.person("Bob Chen", aliases=[_handle("bchen")])
    fixture.fact(alice, "city", "Berlin")

    assert fixture.reader.list_shared_handles() == []
    assert fixture.reader.list_shared_names() == []
    assert fixture.reader.list_conflicting_facts() == []
    assert fixture.reader.list_deleted_person_references() == []


def test_shared_handles_report_both_people_with_their_stored_values() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang", aliases=[_handle("AZhang")])
    alex = fixture.person("Alex Zhang", aliases=[_handle("azhang")])

    usages = fixture.reader.list_shared_handles()

    assert {usage.person.person_id for usage in usages} == {alice.id, alex.id}
    assert {usage.normalized for usage in usages} == {"azhang"}
    assert sorted(usage.value for usage in usages) == ["AZhang", "azhang"]
    assert {usage.source for usage in usages} == {"alias:handle"}


def test_a_handle_shared_with_a_soft_deleted_person_is_not_a_collision() -> None:
    fixture = _Fixture()
    fixture.person("Alice Zhang", aliases=[_handle("azhang")])
    fixture.soft_delete(fixture.person("Old Alice", aliases=[_handle("azhang")]))

    assert fixture.reader.list_shared_handles() == []


def test_shared_names_cover_canonical_and_non_handle_alias_material() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang")
    alex = fixture.person("Alex Zhang", aliases=[Alias(value="alice  zhang", kind=AliasKind.NICKNAME)])

    usages = fixture.reader.list_shared_names()

    assert [(usage.person.person_id, usage.source, usage.value) for usage in usages] == sorted(
        [(alice.id, "canonical_name", "Alice Zhang"), (alex.id, "alias:nickname", "alice  zhang")],
        key=lambda usage: usage[0],
    )
    assert {usage.normalized for usage in usages} == {"alice zhang"}


def test_shared_names_exclude_handles_so_the_two_queries_do_not_double_report() -> None:
    fixture = _Fixture()
    fixture.person("Alice Zhang", aliases=[_handle("azhang")])
    fixture.person("Alex Zhang", aliases=[_handle("azhang")])

    assert fixture.reader.list_shared_names() == []


def test_a_person_whose_alias_repeats_their_own_name_is_not_a_collision() -> None:
    fixture = _Fixture()
    fixture.person("Alice Zhang", aliases=[Alias(value="Alice Zhang", kind=AliasKind.OTHER)])

    assert fixture.reader.list_shared_names() == []


def test_a_name_usage_carries_the_self_flag_the_merge_direction_needs() -> None:
    fixture = _Fixture()
    me = fixture.person("Alice Zhang", is_self=True)
    fixture.person("alice zhang")

    by_person = {usage.person.person_id: usage.person for usage in fixture.reader.list_shared_names()}

    assert by_person[me.id].is_self is True
    assert sum(1 for person in by_person.values() if person.is_self) == 1


def test_conflicting_facts_return_every_row_of_a_multi_valued_predicate_group() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang")
    berlin = fixture.fact(alice, "city", "Berlin", valid_from=date(2020, 1, 1), valid_to=date(2024, 1, 1))
    paris = fixture.fact(alice, "city", "Paris", valid_from=date(2024, 1, 1))
    fixture.fact(alice, "role", "Engineer")

    assertions = fixture.reader.list_conflicting_facts()

    assert {assertion.fact_id for assertion in assertions} == {berlin, paris}
    assert {assertion.predicate for assertion in assertions} == {"city"}
    by_id = {assertion.fact_id: assertion for assertion in assertions}
    assert (by_id[berlin].valid_from, by_id[berlin].valid_to) == (date(2020, 1, 1), date(2024, 1, 1))
    assert (by_id[paris].valid_from, by_id[paris].valid_to) == (date(2024, 1, 1), None)


def test_repeating_the_same_value_is_not_a_candidate_conflict() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang")
    fixture.fact(alice, "city", "Berlin")
    fixture.fact(alice, "city", "Berlin")

    assert fixture.reader.list_conflicting_facts() == []


def test_the_same_predicate_across_two_people_is_not_a_candidate_conflict() -> None:
    fixture = _Fixture()
    fixture.fact(fixture.person("Alice Zhang"), "city", "Berlin")
    fixture.fact(fixture.person("Bob Chen"), "city", "Paris")

    assert fixture.reader.list_conflicting_facts() == []


def test_conflicting_facts_report_their_stored_sensitivity_without_filtering_it() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang")
    fixture.fact(alice, "city", "Berlin", sensitivity=Sensitivity.PUBLIC)
    fixture.fact(alice, "city", "Paris", sensitivity=Sensitivity.RESTRICTED)

    assert {assertion.sensitivity for assertion in fixture.reader.list_conflicting_facts()} == {
        "public",
        "restricted",
    }


def test_facts_of_a_soft_deleted_person_are_not_candidate_conflicts() -> None:
    fixture = _Fixture()
    alice = fixture.person("Alice Zhang")
    fixture.fact(alice, "city", "Berlin")
    fixture.fact(alice, "city", "Paris")
    fixture.soft_delete(alice)

    assert fixture.reader.list_conflicting_facts() == []


def test_every_reference_table_reports_rows_left_pointing_at_a_soft_deleted_person() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    ghost = fixture.person("Ghost")
    relationship_id = fixture.relationship(me, ghost)
    affiliation_id = fixture.affiliation(ghost)
    interaction_id = fixture.interaction(ghost, me, summary="a private dinner")
    fixture.soft_delete(ghost)

    references = fixture.reader.list_deleted_person_references()

    assert {(reference.entity_type, reference.entity_id) for reference in references} == {
        ("relationship", relationship_id),
        ("affiliation", affiliation_id),
        ("interaction", interaction_id),
    }
    assert {reference.person.person_id for reference in references} == {ghost.id}


def test_a_reference_carries_no_record_content() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    ghost = fixture.person("Ghost")
    fixture.interaction(ghost, me, summary="a private dinner")
    fixture.soft_delete(ghost)

    reference = fixture.reader.list_deleted_person_references()[0]

    assert "private dinner" not in repr(reference)
    assert set(vars(reference)) == {"person", "entity_type", "entity_id"}


def test_references_between_active_people_are_not_dangling() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True)
    alice = fixture.person("Alice Zhang")
    fixture.relationship(me, alice)
    fixture.affiliation(alice)
    fixture.interaction(alice, me)

    assert fixture.reader.list_deleted_person_references() == []


def test_a_relationship_between_two_soft_deleted_people_is_reported_for_each_of_them() -> None:
    fixture = _Fixture()
    first = fixture.person("First Ghost")
    second = fixture.person("Second Ghost")
    relationship_id = fixture.relationship(first, second)
    fixture.soft_delete(first)
    fixture.soft_delete(second)

    references = fixture.reader.list_deleted_person_references()

    assert sorted((reference.person.person_id, reference.entity_id) for reference in references) == sorted(
        [(first.id, relationship_id), (second.id, relationship_id)]
    )


def test_every_query_returns_rows_in_a_stable_order() -> None:
    fixture = _Fixture()
    me = fixture.person("Me", is_self=True, aliases=[_handle("shared")])
    alice = fixture.person("Alice Zhang", aliases=[_handle("shared")])
    fixture.person("alice zhang")
    fixture.fact(alice, "city", "Berlin")
    fixture.fact(alice, "city", "Paris")
    ghost = fixture.person("Ghost")
    fixture.relationship(me, ghost)
    fixture.interaction(ghost, me)
    fixture.soft_delete(ghost)

    for _ in range(3):
        assert fixture.reader.list_shared_handles() == fixture.reader.list_shared_handles()
        assert fixture.reader.list_shared_names() == fixture.reader.list_shared_names()
        assert fixture.reader.list_conflicting_facts() == fixture.reader.list_conflicting_facts()
        assert fixture.reader.list_deleted_person_references() == fixture.reader.list_deleted_person_references()
