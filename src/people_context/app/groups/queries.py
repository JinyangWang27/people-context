"""Bounded, disclosure-filtered reads over groups and memberships (M28.1).

These reads describe what is recorded and nothing more: no pairwise connection is derived here.
Disclosure is applied by the store before its limit, so a hidden group or membership cannot
change a visible page, its order, or its `truncated` flag. A group the caller may not see reads
exactly like one that does not exist, and a membership is shown only when its group is visible
too, so a withheld group is never named through one.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field

from people_context.domain.group import Group, GroupKind, GroupMembership
from people_context.domain.shared import Sensitivity
from people_context.ports.groups import GroupStore
from people_context.ports.records import OrganizationStore, RecordReader
from people_context.ports.repository import PersonReader

DEFAULT_GROUP_LIMIT: Final = 50
MIN_GROUP_LIMIT: Final = 1
MAX_GROUP_LIMIT: Final = 200

GROUP_SEARCH_FORMAT: Final = "people-context-group-search"
GROUP_DETAIL_FORMAT: Final = "people-context-group"
PERSON_MEMBERSHIPS_FORMAT: Final = "people-context-person-memberships"
GROUP_DOCUMENT_VERSION: Final = 1


class GroupQueryError(ValueError):
    """Raised when a group read parameter falls outside its documented range."""


class GroupSummary(BaseModel):
    """A group plus the name of the organization it is placed under, when there is one."""

    group: Group
    organization_name: str | None = None


class GroupSearchDocument(BaseModel):
    """Groups whose normalized name contains the query. Matches are candidates, not identity."""

    format: str = GROUP_SEARCH_FORMAT
    version: int = GROUP_DOCUMENT_VERSION
    name: str | None = None
    kind: GroupKind | None = None
    limit: int
    include_sensitive: bool = False
    groups: list[GroupSummary] = Field(default_factory=list)
    truncated: bool = False


class GroupDetailDocument(BaseModel):
    """One group and a bounded page of its visible memberships."""

    format: str = GROUP_DETAIL_FORMAT
    version: int = GROUP_DOCUMENT_VERSION
    found: bool
    group_id: str
    limit: int
    include_sensitive: bool = False
    group: GroupSummary | None = None
    memberships: list[GroupMembership] = Field(default_factory=list)
    truncated: bool = False


class PersonMembershipEntry(BaseModel):
    """One visible membership with the visible group it places the person in."""

    membership: GroupMembership
    group: GroupSummary


class PersonMembershipsDocument(BaseModel):
    """A bounded page of one person's visible memberships."""

    format: str = PERSON_MEMBERSHIPS_FORMAT
    version: int = GROUP_DOCUMENT_VERSION
    found: bool
    person_id: str
    limit: int
    include_sensitive: bool = False
    memberships: list[PersonMembershipEntry] = Field(default_factory=list)
    truncated: bool = False


class FindGroups:
    """List candidate groups by name and kind."""

    def __init__(self, groups: GroupStore, organizations: OrganizationStore) -> None:
        self._groups = groups
        self._organizations = organizations

    def execute(
        self,
        *,
        name: str | None = None,
        kind: GroupKind | None = None,
        limit: int = DEFAULT_GROUP_LIMIT,
        include_sensitive: bool = False,
    ) -> GroupSearchDocument:
        rows = self._groups.find_groups(
            name=name, kind=kind, limit=_checked_limit(limit), sensitivities=_levels(include_sensitive)
        )
        return GroupSearchDocument(
            name=name,
            kind=kind,
            limit=limit,
            include_sensitive=include_sensitive,
            groups=[_summary(self._organizations, group) for group in rows[:limit]],
            truncated=len(rows) > limit,
        )


class GetGroup:
    """Read one visible group and a bounded page of its visible memberships."""

    def __init__(self, records: RecordReader, groups: GroupStore, organizations: OrganizationStore) -> None:
        self._records = records
        self._groups = groups
        self._organizations = organizations

    def execute(
        self, group_id: str, *, limit: int = DEFAULT_GROUP_LIMIT, include_sensitive: bool = False
    ) -> GroupDetailDocument:
        levels = _levels(include_sensitive)
        _checked_limit(limit)
        group = self._records.get_record("group", group_id)
        if not isinstance(group, Group) or group.sensitivity not in levels:
            return GroupDetailDocument(found=False, group_id=group_id, limit=limit, include_sensitive=include_sensitive)
        rows = self._groups.list_group_memberships(group_id, limit=limit, sensitivities=levels)
        return GroupDetailDocument(
            found=True,
            group_id=group_id,
            limit=limit,
            include_sensitive=include_sensitive,
            group=_summary(self._organizations, group),
            memberships=rows[:limit],
            truncated=len(rows) > limit,
        )


class ListPersonMemberships:
    """Read a bounded page of one active person's visible memberships."""

    def __init__(self, people: PersonReader, groups: GroupStore, organizations: OrganizationStore) -> None:
        self._people = people
        self._groups = groups
        self._organizations = organizations

    def execute(
        self, person_id: str, *, limit: int = DEFAULT_GROUP_LIMIT, include_sensitive: bool = False
    ) -> PersonMembershipsDocument:
        _checked_limit(limit)
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return PersonMembershipsDocument(
                found=False, person_id=person_id, limit=limit, include_sensitive=include_sensitive
            )
        rows = self._groups.list_person_memberships(person_id, limit=limit, sensitivities=_levels(include_sensitive))
        return PersonMembershipsDocument(
            found=True,
            person_id=person_id,
            limit=limit,
            include_sensitive=include_sensitive,
            memberships=[
                PersonMembershipEntry(membership=membership, group=_summary(self._organizations, group))
                for membership, group in rows[:limit]
            ],
            truncated=len(rows) > limit,
        )


def _summary(organizations: OrganizationStore, group: Group) -> GroupSummary:
    organization = organizations.get(group.organization_id) if group.organization_id is not None else None
    return GroupSummary(group=group, organization_name=organization.name if organization is not None else None)


def _levels(include_sensitive: bool) -> tuple[Sensitivity, ...]:
    return tuple(Sensitivity) if include_sensitive else (Sensitivity.PUBLIC, Sensitivity.PERSONAL)


def _checked_limit(limit: int) -> int:
    if limit < MIN_GROUP_LIMIT or limit > MAX_GROUP_LIMIT:
        raise GroupQueryError(f"limit must be between {MIN_GROUP_LIMIT} and {MAX_GROUP_LIMIT}")
    return limit
