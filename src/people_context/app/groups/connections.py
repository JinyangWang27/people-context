"""Explain how two people share identified groups, derived at read time (M28.2).

A shared connection is computed from the visible memberships two people hold in the *same*
identified group and is never stored, so a correction, closure, merge, or forget changes the next
read without any cleanup. Nothing here traverses group placement, relationships, or affiliations:
two classes under one school, or two friends of one person, share no group.

Three things stay distinct in the result. A shared context says both people hold a membership in
one group. A derived relation (`classmates`, `teammates`) additionally needs compatible roles and an
established common time. A direct relationship is an assertion someone recorded, returned apart
from both. A missing answer means no supporting shared group was found, not that two people are
strangers.

Established time is only what memberships assert. An absent membership bound is unknown, never
unbounded: overlap is reported only when the known days prove a common day, disjoint only when the
known bounds exclude one, and unknown otherwise.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from people_context.app.groups.queries import (
    DEFAULT_GROUP_LIMIT,
    GroupQueryError,
    GroupSummary,
    _checked_limit,
    _levels,
    _summary,
)
from people_context.domain.group import Group, GroupKind, GroupMembership, MembershipRole, TemporalBasis
from people_context.domain.relationship import Relationship
from people_context.domain.shared import ValidityPeriod, normalize_name
from people_context.ports.clock import Clock
from people_context.ports.context import RelationshipPairReader
from people_context.ports.groups import GroupStore
from people_context.ports.records import OrganizationStore
from people_context.ports.repository import PersonReader

SHARED_CONNECTIONS_FORMAT: Final = "people-context-shared-connections"
SHARED_CONNECTIONS_VERSION: Final = 1

#: Visible memberships read per person. The lookup never lists a group's members, so its work is
#: bounded by this cap whatever the group size; a person with more reports `memberships_truncated`.
MEMBERSHIP_SCAN_LIMIT: Final = 200


class ConnectionKind(StrEnum):
    """Whether a pair only shares a group or also supports a narrower relation label."""

    SHARED_CONTEXT = "shared_context"
    DERIVED_RELATION = "derived_relation"


class TemporalOverlap(StrEnum):
    """What the recorded dates establish about the two memberships' common time."""

    OVERLAP = "overlap"
    DISJOINT = "disjoint"
    UNKNOWN = "unknown"


#: The only derived labels: the group kind and the single role both people must hold.
_LABEL_RULES: Final[dict[GroupKind, tuple[MembershipRole, str]]] = {
    GroupKind.CLASS: (MembershipRole.STUDENT, "classmates"),
    GroupKind.TEAM: (MembershipRole.PARTICIPANT, "teammates"),
}


class SharedConnection(BaseModel):
    """One pair of visible memberships, one per person, in the same visible group."""

    group: GroupSummary
    membership_a: GroupMembership
    membership_b: GroupMembership
    connection: ConnectionKind
    label: str | None = None
    temporal: TemporalOverlap
    overlap: ValidityPeriod | None = None


class SharedConnectionsDocument(BaseModel):
    """Shared group context between two people, plus their direct relationships, bounded."""

    format: str = SHARED_CONNECTIONS_FORMAT
    version: int = SHARED_CONNECTIONS_VERSION
    found: bool
    person_a_id: str
    person_b_id: str
    limit: int
    include_sensitive: bool = False
    connections: list[SharedConnection] = Field(default_factory=list)
    truncated: bool = False
    memberships_truncated: bool = False
    direct_relationships: list[Relationship] = Field(default_factory=list)
    direct_relationships_truncated: bool = False


class ExplainSharedConnections:
    """Derive the shared groups of two active people from their visible memberships."""

    def __init__(
        self,
        people: PersonReader,
        groups: GroupStore,
        organizations: OrganizationStore,
        relationships: RelationshipPairReader,
        clock: Clock,
    ) -> None:
        self._people = people
        self._groups = groups
        self._organizations = organizations
        self._relationships = relationships
        self._clock = clock

    def execute(
        self,
        person_a_id: str,
        person_b_id: str,
        *,
        limit: int = DEFAULT_GROUP_LIMIT,
        include_sensitive: bool = False,
    ) -> SharedConnectionsDocument:
        _checked_limit(limit)
        if person_a_id == person_b_id:
            raise GroupQueryError("person_a and person_b must be two different people")
        document = SharedConnectionsDocument(
            found=False,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
            limit=limit,
            include_sensitive=include_sensitive,
        )
        for person_id in (person_a_id, person_b_id):
            person = self._people.get(person_id)
            if person is None or person.deleted_at is not None:
                return document

        # Disclosure is applied by the store before its limit, so hidden rows never shape the scan.
        levels = _levels(include_sensitive)
        rows_a = self._groups.list_person_memberships(person_a_id, limit=MEMBERSHIP_SCAN_LIMIT, sensitivities=levels)
        rows_b = self._groups.list_person_memberships(person_b_id, limit=MEMBERSHIP_SCAN_LIMIT, sensitivities=levels)
        by_group: dict[str, list[GroupMembership]] = {}
        for membership, _ in rows_b[:MEMBERSHIP_SCAN_LIMIT]:
            by_group.setdefault(membership.group_id, []).append(membership)

        candidates: list[tuple[Group, GroupMembership, GroupMembership]] = [
            (group, membership_a, membership_b)
            for membership_a, group in rows_a[:MEMBERSHIP_SCAN_LIMIT]
            for membership_b in by_group.get(membership_a.group_id, [])
        ]
        candidates.sort(key=lambda row: (normalize_name(row[0].name), row[0].id, row[1].id, row[2].id))
        summaries: dict[str, GroupSummary] = {}
        connections = []
        for group, membership_a, membership_b in candidates[:limit]:
            if group.id not in summaries:
                summaries[group.id] = _summary(self._organizations, group)
            connections.append(_connection(summaries[group.id], membership_a, membership_b))

        direct = self._relationships.list_active_relationships_between(
            person_a_id, person_b_id, self._clock.now().date(), limit
        )
        return document.model_copy(
            update={
                "found": True,
                "connections": connections,
                "truncated": len(candidates) > limit,
                "memberships_truncated": len(rows_a) > MEMBERSHIP_SCAN_LIMIT or len(rows_b) > MEMBERSHIP_SCAN_LIMIT,
                "direct_relationships": direct[:limit],
                "direct_relationships_truncated": len(direct) > limit,
            }
        )


def _connection(group: GroupSummary, membership_a: GroupMembership, membership_b: GroupMembership) -> SharedConnection:
    temporal, overlap = compare_membership_time(membership_a, membership_b)
    label = None
    rule = _LABEL_RULES.get(group.group.kind)
    if rule is not None and temporal is TemporalOverlap.OVERLAP and membership_a.role is membership_b.role is rule[0]:
        label = rule[1]
    return SharedConnection(
        group=group,
        membership_a=membership_a,
        membership_b=membership_b,
        connection=ConnectionKind.DERIVED_RELATION if label else ConnectionKind.SHARED_CONTEXT,
        label=label,
        temporal=temporal,
        overlap=overlap,
    )


def compare_membership_time(
    first: GroupMembership, second: GroupMembership
) -> tuple[TemporalOverlap, ValidityPeriod | None]:
    """Classify two memberships' common time, returning the established overlap when there is one."""
    known_first, known_second = _established(first), _established(second)
    if known_first is not None and known_second is not None:
        start, end = max(known_first[0], known_second[0]), min(known_first[1], known_second[1])
        if start <= end:
            return TemporalOverlap.OVERLAP, ValidityPeriod(valid_from=start, valid_to=end)
    possible_first, possible_second = _possible(first), _possible(second)
    if max(possible_first[0], possible_second[0]) > min(possible_first[1], possible_second[1]):
        return TemporalOverlap.DISJOINT, None
    return TemporalOverlap.UNKNOWN, None


def _established(membership: GroupMembership) -> tuple[date, date] | None:
    """The days the membership is asserted to have held; `None` when no day is known.

    One known bound proves only that day. An `ongoing` membership was current when it was recorded,
    which proves the days from its start to that record date, and nothing after it. A start later than
    the record date contradicts that assertion, so it establishes no day at all.
    """
    start, end = membership.period.valid_from, membership.period.valid_to
    if membership.temporal_basis is TemporalBasis.ONGOING:
        recorded = membership.created_at.date()
        if start is None:
            return (recorded, recorded)
        return (start, recorded) if start <= recorded else None
    if membership.temporal_basis is not TemporalBasis.PERIOD:
        return None
    if start is None:
        return (end, end) if end is not None else None
    return (start, end if end is not None else start)


def _possible(membership: GroupMembership) -> tuple[date, date]:
    """The widest span consistent with the record: an unknown bound is left open."""
    start, end = membership.period.valid_from, membership.period.valid_to
    if membership.temporal_basis is TemporalBasis.UNKNOWN:
        return date.min, date.max
    if membership.temporal_basis is TemporalBasis.ONGOING:
        return start or date.min, date.max
    return start or date.min, end or date.max
