"""Application policy for groups and memberships (M28.1), against in-memory ports."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from people_context.app.groups.commands import (
    CLOSURE_ALREADY_ENDED,
    CLOSURE_ENDS_BEFORE_START,
    AddGroupMembership,
    AddGroupMembershipInput,
    CloseGroupMembership,
    CloseGroupMembershipInput,
    CreateGroup,
    CreateGroupInput,
    InvalidMembershipClosureError,
)
from people_context.app.groups.queries import (
    FindGroups,
    GetGroup,
    GroupQueryError,
    ListPersonMemberships,
)
from people_context.app.records import (
    CorrectRecord,
    CorrectRecordInput,
    InvalidCorrectionError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
)
from people_context.domain.group import GroupKind, GroupMembership, MembershipRole, TemporalBasis
from people_context.domain.organization import Organization
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from tests.app.fakes import (
    FakeAuditLog,
    FakeClock,
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
        self.audit = FakeAuditLog()
        self.clock = FakeClock(_NOW)
        for person_id, name in (("P1", "Alice"), ("P2", "Bob")):
            self.people.save_person(Person(id=person_id, canonical_name=name))
        self.create = CreateGroup(self.groups, self.organizations, self.audit, self.clock)
        self.add = AddGroupMembership(self.people, self.records, self.groups, self.audit, self.clock)
        self.close = CloseGroupMembership(self.records, self.records, self.audit, self.clock)
        self.correct = CorrectRecord(self.records, self.records, self.audit, self.clock, people=self.people)
        self.find = FindGroups(self.groups, self.organizations)
        self.get = GetGroup(self.records, self.groups, self.organizations)
        self.memberships = ListPersonMemberships(self.people, self.groups, self.organizations)

    def group(self, name: str = "Class 1", **fields: object) -> str:
        return self.create.execute(CreateGroupInput(name=name, kind=GroupKind.CLASS, **fields)).id  # type: ignore[arg-type]

    def member(self, group_id: str, person_id: str = "P1", **fields: object) -> GroupMembership:
        return self.add.execute(AddGroupMembershipInput(person_id=person_id, group_id=group_id, **fields))  # type: ignore[arg-type]


class TestCreate:
    def test_an_equal_name_creates_a_distinct_group(self) -> None:
        store = _Store()

        first, second = store.group("Class 1"), store.group("class 1")

        assert first != second
        assert [entry.entity_type for entry in store.audit.entries] == ["group", "group"]
        assert {entry.op for entry in store.audit.entries} == {"create"}

    def test_an_unknown_organization_is_refused_and_nothing_is_written(self) -> None:
        store = _Store()

        with pytest.raises(OrganizationNotFoundError):
            store.group(organization_id="ORG-missing")

        assert store.audit.entries == []
        assert store.organizations.organizations == {}

    def test_placement_names_an_existing_organization_without_writing_it(self) -> None:
        store = _Store()
        store.organizations.save(Organization(id="ORG1", name="Northside School"))

        document = store.get.execute(store.group(organization_id="ORG1"))

        assert document.group is not None and document.group.organization_name == "Northside School"
        assert [entry.entity_type for entry in store.audit.entries] == ["group"]

    def test_a_blank_name_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _Store().group("   ")


class TestMembership:
    def test_basis_is_inferred_from_dates_but_ongoing_must_be_stated(self) -> None:
        store = _Store()
        group_id = store.group()

        undated = store.member(group_id)
        dated = store.member(group_id, valid_from=date(2015, 9, 1))
        ongoing = store.member(group_id, valid_from=date(2020, 1, 1), temporal_basis=TemporalBasis.ONGOING)

        assert undated.temporal_basis is TemporalBasis.UNKNOWN
        assert dated.temporal_basis is TemporalBasis.PERIOD and dated.period.valid_to is None
        assert ongoing.temporal_basis is TemporalBasis.ONGOING

    @pytest.mark.parametrize(
        ("basis", "valid_from", "valid_to"),
        [
            (TemporalBasis.UNKNOWN, date(2015, 9, 1), None),
            (TemporalBasis.PERIOD, None, None),
            (TemporalBasis.ONGOING, None, date(2016, 7, 1)),
        ],
    )
    def test_a_basis_contradicting_its_dates_is_refused(
        self, basis: TemporalBasis, valid_from: date | None, valid_to: date | None
    ) -> None:
        store = _Store()
        group_id = store.group()

        with pytest.raises(ValidationError):
            store.member(group_id, temporal_basis=basis, valid_from=valid_from, valid_to=valid_to)

    def test_unknown_person_and_group_are_refused(self) -> None:
        store = _Store()
        group_id = store.group()

        with pytest.raises(PersonNotFoundError):
            store.member(group_id, person_id="P-missing")
        with pytest.raises(RecordNotFoundError):
            store.member("G-missing")

    def test_concurrent_and_historical_memberships_are_all_kept(self) -> None:
        store = _Store()
        group_id = store.group()

        store.member(group_id, role=MembershipRole.STUDENT, valid_from=date(2015, 9, 1), valid_to=date(2016, 7, 1))
        store.member(group_id, role=MembershipRole.LEADER, valid_from=date(2015, 9, 1))

        assert len(store.memberships.execute("P1").memberships) == 2


class TestClosure:
    def test_closure_records_the_end_and_keeps_what_was_asserted(self) -> None:
        store = _Store()
        membership = store.member(store.group(), valid_from=date(2020, 1, 1), temporal_basis=TemporalBasis.ONGOING)

        closed = store.close.execute(CloseGroupMembershipInput(membership_id=membership.id, ended_on=date(2024, 6, 30)))

        assert closed.period.valid_from == date(2020, 1, 1)
        assert closed.period.valid_to == date(2024, 6, 30)
        assert closed.temporal_basis is TemporalBasis.PERIOD
        entry = store.audit.entries[-1]
        assert (entry.op, entry.entity_type) == ("update", "group_membership")
        assert entry.payload["before"]["period"]["valid_to"] is None
        assert entry.payload["fields"] == ["temporal_basis", "valid_to"]

    def test_an_undated_membership_can_close_with_an_unknown_start(self) -> None:
        store = _Store()
        membership = store.member(store.group())

        closed = store.close.execute(CloseGroupMembershipInput(membership_id=membership.id, ended_on=date(2024, 6, 30)))

        assert (closed.period.valid_from, closed.temporal_basis) == (None, TemporalBasis.PERIOD)

    @pytest.mark.parametrize(
        ("valid_to", "ended_on", "reason"),
        [
            (date(2016, 7, 1), date(2016, 8, 1), CLOSURE_ALREADY_ENDED),
            (None, date(2015, 8, 31), CLOSURE_ENDS_BEFORE_START),
        ],
    )
    def test_closure_refuses_rather_than_rewriting(self, valid_to: date | None, ended_on: date, reason: str) -> None:
        store = _Store()
        membership = store.member(store.group(), valid_from=date(2015, 9, 1), valid_to=valid_to)
        entries = len(store.audit.entries)

        with pytest.raises(InvalidMembershipClosureError) as raised:
            store.close.execute(CloseGroupMembershipInput(membership_id=membership.id, ended_on=ended_on))

        assert raised.value.reason == reason
        assert len(store.audit.entries) == entries

    def test_an_unknown_membership_is_refused(self) -> None:
        with pytest.raises(RecordNotFoundError):
            _Store().close.execute(CloseGroupMembershipInput(membership_id="M-missing", ended_on=date(2024, 1, 1)))


class TestCorrection:
    def test_an_erroneous_end_is_corrected_with_before_and_after(self) -> None:
        store = _Store()
        membership = store.member(store.group(), valid_from=date(2015, 9, 1), valid_to=date(2016, 7, 1))

        corrected = store.correct.execute(
            CorrectRecordInput(
                entity_type="group_membership", entity_id=membership.id, fields={"valid_to": "2017-07-01"}
            )
        )

        assert isinstance(corrected, GroupMembership) and corrected.period.valid_to == date(2017, 7, 1)
        assert store.audit.entries[-1].op == "correct"

    def test_a_correction_contradicting_the_basis_is_refused(self) -> None:
        store = _Store()
        membership = store.member(store.group())

        with pytest.raises(InvalidCorrectionError):
            store.correct.execute(
                CorrectRecordInput(
                    entity_type="group_membership", entity_id=membership.id, fields={"valid_from": "2015-09-01"}
                )
            )

    def test_placement_and_identity_links_are_not_correctable(self) -> None:
        store = _Store()
        group_id = store.group()
        membership = store.member(group_id)

        with pytest.raises(InvalidCorrectionError):
            store.correct.execute(
                CorrectRecordInput(entity_type="group", entity_id=group_id, fields={"organization_id": "X"})
            )
        with pytest.raises(InvalidCorrectionError):
            store.correct.execute(
                CorrectRecordInput(entity_type="group_membership", entity_id=membership.id, fields={"person_id": "P2"})
            )


class TestDisclosure:
    def test_a_hidden_group_reads_as_missing_and_hides_its_memberships(self) -> None:
        store = _Store()
        hidden = store.group("Support circle", sensitivity=Sensitivity.SENSITIVE)
        store.member(hidden)

        assert store.get.execute(hidden).found is False
        assert store.find.execute(name="support").groups == []
        assert store.memberships.execute("P1").memberships == []
        assert store.get.execute(hidden, include_sensitive=True).found is True
        assert len(store.memberships.execute("P1", include_sensitive=True).memberships) == 1

    def test_hidden_rows_never_change_a_visible_page_or_its_truncation(self) -> None:
        store = _Store()
        group_id = store.group()
        visible = store.member(group_id, valid_from=date(2015, 9, 1))
        store.member(group_id, person_id="P2", valid_from=date(2014, 9, 1), sensitivity=Sensitivity.RESTRICTED)

        document = store.get.execute(group_id, limit=1)

        assert [row.id for row in document.memberships] == [visible.id]
        assert document.truncated is False
        assert store.get.execute(group_id, limit=1, include_sensitive=True).truncated is True

    @pytest.mark.parametrize("limit", [0, 201])
    def test_limits_outside_the_range_are_refused(self, limit: int) -> None:
        store = _Store()
        with pytest.raises(GroupQueryError):
            store.find.execute(limit=limit)
        with pytest.raises(GroupQueryError):
            store.get.execute("G", limit=limit)
        with pytest.raises(GroupQueryError):
            store.memberships.execute("P1", limit=limit)

    def test_an_unknown_person_is_not_found(self) -> None:
        assert _Store().memberships.execute("P-missing").found is False
