"""Identified groups and the membership assertions that place people in them (M28.1).

A group is a context people participate in — a class, a team, a household — not proof that its
members know one another. Three things are kept apart: the group's identity, a membership
assertion, and any connection a later read might derive from memberships. Only the first two are
stored.

Equal names are never identity. Two groups called "Class 1" are distinct rows until someone
resolves them explicitly, which is why nothing here offers get-or-create by name.

Membership dates carry an explicit basis because `ValidityPeriod` reads an absent bound as
unbounded for filtering, and that reading must not become evidence that two people were in the
same place at the same time. A missing bound on a membership is unknown, never open-ended.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, Field, StringConstraints, model_validator

from people_context.domain.shared import (
    Confidence,
    Provenance,
    Sensitivity,
    ValidityPeriod,
    new_id,
    utc_now,
)

#: Characters a group name may carry. A name identifies a context to a reader; a longer value
#: is a description or a pasted passage, neither of which belongs in an identity column.
MAX_GROUP_NAME_CHARS: Final = 256

GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_GROUP_NAME_CHARS)]


class GroupKind(StrEnum):
    """What kind of context a group is."""

    CLASS = "class"
    COHORT = "cohort"
    TEAM = "team"
    DEPARTMENT = "department"
    CLUB = "club"
    HOUSEHOLD = "household"
    COMMUNITY = "community"
    OTHER = "other"


class MembershipRole(StrEnum):
    """The part a person plays in a group. A teacher and a pupil of one class are not peers."""

    MEMBER = "member"
    STUDENT = "student"
    TEACHER = "teacher"
    PARTICIPANT = "participant"
    LEADER = "leader"
    STAFF = "staff"
    OTHER = "other"


class TemporalBasis(StrEnum):
    """What a membership's dates actually assert.

    - `unknown`: nothing is known about when; both dates are absent.
    - `period`: at least one bound is known. An absent bound is unknown, not unbounded.
    - `ongoing`: asserted current when recorded, so there is no end date.
    """

    UNKNOWN = "unknown"
    PERIOD = "period"
    ONGOING = "ongoing"


def resolve_temporal_basis(
    declared: TemporalBasis | None,
    valid_from: date | None,
    valid_to: date | None,
) -> TemporalBasis:
    """Return the basis a membership asserts when the caller did not state one.

    No date is `unknown`; any date is `period`. `ongoing` is never inferred, because an absent
    end date is not a claim that the membership continues — it is the absence of a claim. Both
    the direct write and agent staging read this one rule, so a staged membership carries the
    same basis the record it becomes would have carried.
    """
    if declared is not None:
        return declared
    return TemporalBasis.UNKNOWN if valid_from is None and valid_to is None else TemporalBasis.PERIOD


def check_temporal_basis(basis: TemporalBasis, valid_from: date | None, valid_to: date | None) -> None:
    """Raise ``ValueError`` when dates and basis contradict each other.

    Shared by the durable membership and the persisted staged candidate: a restored candidate
    that claimed `unknown` while carrying dates would commit into a record this refuses, and the
    refusal would land mid-transaction rather than when the bundle could still be declined.
    """
    has_dates = valid_from is not None or valid_to is not None
    if basis is TemporalBasis.UNKNOWN and has_dates:
        raise ValueError("temporal_basis 'unknown' cannot carry dates")
    if basis is TemporalBasis.PERIOD and not has_dates:
        raise ValueError("temporal_basis 'period' needs valid_from or valid_to")
    if basis is TemporalBasis.ONGOING and valid_to is not None:
        raise ValueError("temporal_basis 'ongoing' cannot carry valid_to")


class Group(BaseModel):
    """One identified group, optionally placed under an existing organization.

    Placement is context only: it creates no affiliation, and the organization row is never
    written from here, so a protected group name cannot leak into an unrestricted table.
    """

    id: str = Field(default_factory=new_id)
    name: GroupName
    kind: GroupKind
    organization_id: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)


class GroupMembership(BaseModel):
    """One assertion that a person held a role in a group, with explicit temporal evidence."""

    id: str = Field(default_factory=new_id)
    person_id: str
    group_id: str
    role: MembershipRole = MembershipRole.MEMBER
    period: ValidityPeriod = Field(default_factory=ValidityPeriod)
    temporal_basis: TemporalBasis = TemporalBasis.UNKNOWN
    confidence: Confidence = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _check_basis(self) -> GroupMembership:
        check_temporal_basis(self.temporal_basis, self.period.valid_from, self.period.valid_to)
        return self
