"""Record, close, and place people in identified groups (M28.1).

Every write is one transaction through `audit_mutation`. Creating a group and adding a member are
separate transactions on purpose: hard forget redacts a whole transaction that mentions an erased
id, so a member's erasure must not take the group's own creation history with it.

Closing a membership and correcting one are different acts. Closure records that a historically
true membership ended; it refuses to overwrite an end that is already recorded, because replacing
a known end date is a correction of an error, and `correct_record` is the tool that says so.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from pydantic import BaseModel

from people_context.app._mutation import (
    OrganizationNotFoundError,
    RecordNotFoundError,
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.group import Group, GroupKind, GroupMembership, MembershipRole, TemporalBasis
from people_context.domain.shared import Confidence, Sensitivity, ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.groups import GroupStore
from people_context.ports.records import OrganizationStore, RecordReader, RecordWriter
from people_context.ports.repository import PersonReader

#: The membership already records an end; replacing it is a correction, not a closure.
CLOSURE_ALREADY_ENDED: Final = "already_ended"
#: The end would fall before the recorded start.
CLOSURE_ENDS_BEFORE_START: Final = "ends_before_start"


class InvalidMembershipClosureError(Exception):
    """Raised when a closure would contradict what the membership already records."""

    def __init__(self, membership_id: str, reason: str) -> None:
        self.membership_id = membership_id
        self.reason = reason
        super().__init__(f"cannot close membership {membership_id}: {reason}")


class CreateGroupInput(BaseModel):
    """Input for a new identified group. A matching name never reuses an existing group."""

    name: str
    kind: GroupKind
    organization_id: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class CreateGroup:
    """Create one group, optionally placed under an existing organization."""

    def __init__(self, groups: GroupStore, organizations: OrganizationStore, audit: AuditLog, clock: Clock) -> None:
        self._groups = groups
        self._organizations = organizations
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: CreateGroupInput) -> Group:
        if data.organization_id is not None and self._organizations.get(data.organization_id) is None:
            raise OrganizationNotFoundError(data.organization_id)
        group = Group(
            name=data.name,
            kind=data.kind,
            organization_id=data.organization_id,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._groups.save_group(group)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="group",
            entity_id=group.id,
            payload=snapshot(group),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return group


class AddGroupMembershipInput(BaseModel):
    """Input for one membership assertion.

    Without an explicit basis, dates decide it: none is `unknown`, any is `period`. `ongoing` is
    never inferred, because an absent end date is not a claim that the membership continues.
    """

    person_id: str
    group_id: str
    role: MembershipRole = MembershipRole.MEMBER
    valid_from: date | None = None
    valid_to: date | None = None
    temporal_basis: TemporalBasis | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class AddGroupMembership:
    """Record that an active person held a role in an existing group."""

    def __init__(
        self,
        people: PersonReader,
        records: RecordReader,
        groups: GroupStore,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._people = people
        self._records = records
        self._groups = groups
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: AddGroupMembershipInput) -> GroupMembership:
        require_active_person(self._people, data.person_id)
        if self._records.get_record("group", data.group_id) is None:
            raise RecordNotFoundError("group", data.group_id)
        basis = data.temporal_basis
        if basis is None:
            basis = TemporalBasis.UNKNOWN if data.valid_from is None and data.valid_to is None else TemporalBasis.PERIOD
        membership = GroupMembership(
            person_id=data.person_id,
            group_id=data.group_id,
            role=data.role,
            period=ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to),
            temporal_basis=basis,
            confidence=data.confidence if data.confidence is not None else 1.0,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._groups.save_membership(membership)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="group_membership",
            entity_id=membership.id,
            payload=snapshot(membership),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return membership


class CloseGroupMembershipInput(BaseModel):
    """Input recording the last day a historically true membership held."""

    membership_id: str
    ended_on: date
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class CloseGroupMembership:
    """End a membership without rewriting what it previously asserted."""

    def __init__(self, records: RecordReader, writer: RecordWriter, audit: AuditLog, clock: Clock) -> None:
        self._records = records
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: CloseGroupMembershipInput) -> GroupMembership:
        current = self._records.get_record("group_membership", data.membership_id)
        if not isinstance(current, GroupMembership):
            raise RecordNotFoundError("group_membership", data.membership_id)
        if current.period.valid_to is not None:
            raise InvalidMembershipClosureError(data.membership_id, CLOSURE_ALREADY_ENDED)
        if current.period.valid_from is not None and data.ended_on < current.period.valid_from:
            raise InvalidMembershipClosureError(data.membership_id, CLOSURE_ENDS_BEFORE_START)
        fields = {"valid_to": data.ended_on, "temporal_basis": TemporalBasis.PERIOD}
        updated = self._writer.update_record_fields("group_membership", data.membership_id, fields)
        if not isinstance(updated, GroupMembership):
            raise RecordNotFoundError("group_membership", data.membership_id)
        audit_mutation(
            self._audit,
            self._clock,
            op="update",
            entity_type="group_membership",
            entity_id=data.membership_id,
            payload={"before": snapshot(current), "after": snapshot(updated), "fields": sorted(fields)},
            replay_payload=snapshot(updated),
            changed_fields=sorted(fields),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )
        return updated
