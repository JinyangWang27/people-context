"""Application policy for explained shared connections (M28.2), against in-memory ports."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from people_context.app.groups.commands import (
    AddGroupMembership,
    AddGroupMembershipInput,
    CreateGroup,
    CreateGroupInput,
)
from people_context.app.groups.connections import (
    MEMBERSHIP_SCAN_LIMIT,
    SHARED_CONNECTIONS_FORMAT,
    ConnectionKind,
    ExplainSharedConnections,
    SharedConnectionsDocument,
    TemporalOverlap,
)
from people_context.app.groups.queries import GroupQueryError
from people_context.domain.group import GroupKind, GroupMembership, MembershipRole, TemporalBasis
from people_context.domain.organization import Organization
from people_context.domain.person import Person
from people_context.domain.relationship import Relationship
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod
from people_context.ports.context import RelationshipRecord
from tests.app.fakes import (
    FakeAuditLog,
    FakeClock,
    FakeContextReader,
    FakeGroupStore,
    FakeOrganizationStore,
    FakePeopleRepository,
    FakeRecordStore,
)

_NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


class _Store:
    def __init__(self) -> None:
        self.people = FakePeopleRepository()
        self.records = FakeRecordStore()
        self.groups = FakeGroupStore(self.records)
        self.organizations = FakeOrganizationStore()
        self.context = FakeContextReader()
        self.audit = FakeAuditLog()
        self.clock = FakeClock(_NOW)
        for person_id, name in (("A", "Alice"), ("B", "Bob"), ("C", "Carol")):
            self.people.save_person(Person(id=person_id, canonical_name=name))
        self.create = CreateGroup(self.groups, self.organizations, self.audit, self.clock)
        self.add = AddGroupMembership(self.people, self.records, self.groups, self.audit, self.clock)
        self.explain = ExplainSharedConnections(
            self.people, self.groups, self.organizations, self.context, self.clock
        )

    def group(self, name: str, kind: GroupKind = GroupKind.CLASS, **fields: object) -> str:
        return self.create.execute(CreateGroupInput(name=name, kind=kind, **fields)).id  # type: ignore[arg-type]

    def member(self, group_id: str, person_id: str, **fields: object) -> GroupMembership:
        return self.add.execute(AddGroupMembershipInput(person_id=person_id, group_id=group_id, **fields))  # type: ignore[arg-type]

    def run(self, a: str = "A", b: str = "B", **options: object) -> SharedConnectionsDocument:
        return self.explain.execute(a, b, **options)  # type: ignore[arg-type]


def _students(store: _Store, group_id: str, a: tuple[str, str | None], b: tuple[str, str | None]) -> None:
    store.member(group_id, "A", role=MembershipRole.STUDENT, valid_from=a[0], valid_to=a[1])
    store.member(group_id, "B", role=MembershipRole.STUDENT, valid_from=b[0], valid_to=b[1])


class TestLabels:
    def test_students_of_one_class_with_proven_overlap_are_classmates(self) -> None:
        store = _Store()
        class_id = store.group("Class 1, Grade 6")
        _students(store, class_id, ("2015-09-01", "2016-06-30"), ("2016-01-10", "2016-07-15"))

        document = store.run()

        assert document.format == SHARED_CONNECTIONS_FORMAT and document.found
        [connection] = document.connections
        assert connection.connection is ConnectionKind.DERIVED_RELATION
        assert connection.label == "classmates"
        assert connection.temporal is TemporalOverlap.OVERLAP
        assert connection.overlap == ValidityPeriod(valid_from=date(2016, 1, 10), valid_to=date(2016, 6, 30))
        assert (connection.membership_a.person_id, connection.membership_b.person_id) == ("A", "B")

    def test_disjoint_dates_in_one_class_are_historical_context_only(self) -> None:
        store = _Store()
        class_id = store.group("Class 1")
        _students(store, class_id, ("2015-09-01", "2016-06-30"), ("2017-09-01", "2018-06-30"))

        [connection] = store.run().connections

        assert (connection.connection, connection.label) == (ConnectionKind.SHARED_CONTEXT, None)
        assert (connection.temporal, connection.overlap) == (TemporalOverlap.DISJOINT, None)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ({}, {}),
            ({}, {"valid_from": "2015-09-01", "valid_to": "2016-06-30"}),
            # One known start each proves only that day; later days are unknown, not unbounded.
            ({"valid_from": "2015-09-01"}, {"valid_from": "2016-01-10"}),
            ({"valid_to": "2016-06-30"}, {"valid_to": "2016-01-10"}),
        ],
    )
    def test_absent_bounds_never_prove_overlap(self, a: dict[str, str], b: dict[str, str]) -> None:
        store = _Store()
        class_id = store.group("Class 1")
        store.member(class_id, "A", role=MembershipRole.STUDENT, **a)
        store.member(class_id, "B", role=MembershipRole.STUDENT, **b)

        [connection] = store.run().connections

        assert (connection.temporal, connection.label) == (TemporalOverlap.UNKNOWN, None)
        assert connection.connection is ConnectionKind.SHARED_CONTEXT

    def test_one_known_day_inside_a_known_period_is_overlap(self) -> None:
        store = _Store()
        class_id = store.group("Class 1")
        _students(store, class_id, ("2015-09-01", "2016-06-30"), ("2016-01-10", None))

        [connection] = store.run().connections

        assert connection.label == "classmates"
        assert connection.overlap == ValidityPeriod(valid_from=date(2016, 1, 10), valid_to=date(2016, 1, 10))

    def test_an_open_start_after_a_known_end_is_disjoint(self) -> None:
        store = _Store()
        class_id = store.group("Class 1")
        _students(store, class_id, ("2015-09-01", "2016-06-30"), ("2017-09-01", None))

        assert store.run().connections[0].temporal is TemporalOverlap.DISJOINT

    def test_ongoing_memberships_prove_only_their_recorded_days(self) -> None:
        store = _Store()
        team = store.group("Platform", GroupKind.TEAM)
        store.member(team, "A", role=MembershipRole.PARTICIPANT, temporal_basis=TemporalBasis.ONGOING)
        store.clock.set(_NOW + timedelta(days=30))
        store.member(team, "B", role=MembershipRole.PARTICIPANT, temporal_basis=TemporalBasis.ONGOING)

        assert store.run().connections[0].temporal is TemporalOverlap.UNKNOWN

    def test_ongoing_since_a_start_overlaps_a_later_record(self) -> None:
        store = _Store()
        team = store.group("Platform", GroupKind.TEAM)
        store.member(
            team, "A", role=MembershipRole.PARTICIPANT, valid_from="2024-01-01", temporal_basis=TemporalBasis.ONGOING
        )
        store.clock.set(_NOW + timedelta(days=30))
        store.member(
            team, "B", role=MembershipRole.PARTICIPANT, valid_from="2025-03-01", temporal_basis=TemporalBasis.ONGOING
        )

        [connection] = store.run().connections

        assert connection.label == "teammates"
        assert connection.overlap == ValidityPeriod(valid_from=date(2025, 3, 1), valid_to=_NOW.date())

    @pytest.mark.parametrize("other", [{"temporal_basis": TemporalBasis.ONGOING}, {"valid_to": "2027-06-30"}])
    def test_an_ongoing_start_after_its_record_date_establishes_no_future_day(self, other: dict[str, object]) -> None:
        store = _Store()
        team = store.group("Platform", GroupKind.TEAM)
        future = {"role": MembershipRole.PARTICIPANT, "valid_from": "2027-01-01"}
        store.member(team, "A", temporal_basis=TemporalBasis.ONGOING, **future)
        store.member(team, "B", **future, **other)

        [connection] = store.run().connections

        assert (connection.temporal, connection.label, connection.overlap) == (TemporalOverlap.UNKNOWN, None, None)

    @pytest.mark.parametrize(
        ("kind", "roles"),
        [
            (GroupKind.CLASS, (MembershipRole.TEACHER, MembershipRole.STUDENT)),
            (GroupKind.TEAM, (MembershipRole.MEMBER, MembershipRole.LEADER)),
            (GroupKind.COHORT, (MembershipRole.STUDENT, MembershipRole.STUDENT)),
            (GroupKind.CLUB, (MembershipRole.MEMBER, MembershipRole.MEMBER)),
            (GroupKind.HOUSEHOLD, (MembershipRole.MEMBER, MembershipRole.MEMBER)),
            (GroupKind.COMMUNITY, (MembershipRole.MEMBER, MembershipRole.MEMBER)),
        ],
    )
    def test_other_kinds_and_roles_give_context_without_a_label(
        self, kind: GroupKind, roles: tuple[MembershipRole, MembershipRole]
    ) -> None:
        store = _Store()
        group_id = store.group("Shared", kind)
        period = {"valid_from": "2020-01-01", "valid_to": "2021-01-01"}
        store.member(group_id, "A", role=roles[0], **period)
        store.member(group_id, "B", role=roles[1], **period)

        [connection] = store.run().connections

        assert connection.temporal is TemporalOverlap.OVERLAP
        assert (connection.connection, connection.label) == (ConnectionKind.SHARED_CONTEXT, None)


class TestScope:
    def test_same_school_group_with_different_classes_gives_only_school_context(self) -> None:
        store = _Store()
        store.organizations.save(Organization(id="O1", name="North School", name_normalized="north school"))
        school = store.group("North School students", GroupKind.COMMUNITY, organization_id="O1")
        first = store.group("Class 1", organization_id="O1")
        second = store.group("Class 2", organization_id="O1")
        period = {"role": MembershipRole.STUDENT, "valid_from": "2015-09-01", "valid_to": "2016-06-30"}
        for person_id, class_id in (("A", first), ("B", second)):
            store.member(school, person_id, **period)
            store.member(class_id, person_id, **period)

        [connection] = store.run().connections

        assert connection.group.group.id == school
        assert connection.group.organization_name == "North School"
        assert connection.label is None

    def test_groups_under_one_organization_and_a_common_person_share_nothing(self) -> None:
        store = _Store()
        store.organizations.save(Organization(id="O1", name="Acme", name_normalized="acme"))
        store.member(store.group("Team X", GroupKind.TEAM, organization_id="O1"), "A")
        store.member(store.group("Team Y", GroupKind.TEAM, organization_id="O1"), "B")
        for other in ("A", "B"):
            store.context.relationships.append(
                RelationshipRecord(
                    relationship=Relationship(
                        subject_id=other, object_id="C", type="classmate_of", provenance=Provenance(source="t")
                    ),
                    other_person_id="C",
                    other_person_name="Carol",
                )
            )

        document = store.run()

        assert document.found and document.connections == [] and document.direct_relationships == []

    def test_direct_relationships_are_returned_apart_from_derived_results(self) -> None:
        store = _Store()
        relationship = Relationship(subject_id="B", object_id="A", type="friend_of", provenance=Provenance(source="t"))
        store.context.relationships.append(
            RelationshipRecord(relationship=relationship, other_person_id="B", other_person_name="Bob")
        )

        document = store.run()

        assert document.connections == []
        assert [row.id for row in document.direct_relationships] == [relationship.id]

    def test_concurrent_memberships_in_one_group_each_pair_in_stable_order(self) -> None:
        store = _Store()
        later = store.group("b group", GroupKind.CLUB)
        earlier = store.group("A group", GroupKind.CLUB)
        for group_id in (later, earlier):
            store.member(group_id, "A")
            store.member(group_id, "B")
        store.member(earlier, "A", role=MembershipRole.LEADER)

        first, second = store.run(), store.run()

        assert first == second
        assert [row.group.group.id for row in first.connections] == [earlier, earlier, later]
        pairs = [(row.membership_a.id, row.membership_b.id) for row in first.connections[:2]]
        assert pairs == sorted(pairs)


class TestDisclosureAndBounds:
    def test_hidden_groups_and_memberships_do_not_change_results_or_signals(self) -> None:
        store = _Store()
        visible = store.group("Club", GroupKind.CLUB)
        store.member(visible, "A")
        store.member(visible, "B")
        baseline = store.run(limit=1)

        secret = store.group("Aaa secret", GroupKind.CLUB, sensitivity=Sensitivity.SENSITIVE)
        store.member(secret, "A")
        store.member(secret, "B")
        public = store.group("Aaa public", GroupKind.CLUB, sensitivity=Sensitivity.PUBLIC)
        store.member(public, "A", sensitivity=Sensitivity.RESTRICTED)
        store.member(public, "B")

        assert store.run(limit=1) == baseline
        assert not baseline.truncated
        revealed = store.run(limit=1, include_sensitive=True)
        assert revealed.truncated and revealed.connections[0].group.group.id in {secret, public}

    def test_limit_truncates_pairs_and_reports_it(self) -> None:
        store = _Store()
        for name in ("One", "Two", "Three"):
            group_id = store.group(name, GroupKind.CLUB)
            store.member(group_id, "A")
            store.member(group_id, "B")

        document = store.run(limit=2)

        assert len(document.connections) == 2 and document.truncated
        assert not store.run(limit=3).truncated

    def test_membership_scan_cap_reports_a_partial_result(self) -> None:
        store = _Store()
        shared = store.group("Shared", GroupKind.CLUB)
        store.member(shared, "A")
        store.member(shared, "B")
        for index in range(MEMBERSHIP_SCAN_LIMIT):
            store.member(store.group(f"Solo {index}", GroupKind.CLUB), "A", valid_from="2000-01-01")

        document = store.run()

        assert document.memberships_truncated
        assert document.connections == []

    def test_invalid_requests_and_missing_people(self) -> None:
        store = _Store()
        with pytest.raises(GroupQueryError):
            store.run("A", "A")
        with pytest.raises(GroupQueryError):
            store.run(limit=0)
        assert not store.run("A", "nobody").found
        store.people.save_person(Person(id="B", canonical_name="Bob", deleted_at=_NOW))
        assert not store.run().found
