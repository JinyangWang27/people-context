"""Narrow persistence port for identified groups and memberships (M28.1)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from people_context.domain.group import Group, GroupKind, GroupMembership
from people_context.domain.shared import Sensitivity


@runtime_checkable
class GroupStore(Protocol):
    """Write groups and memberships, and read them back under a disclosure filter.

    Every list read takes the levels the caller may disclose and applies them *before* its limit,
    reading one row past `limit` so the caller can report truncation. A hidden row therefore never
    changes what a visible page contains or whether it looks truncated. A membership is disclosed
    only when its group is too, so a withheld group is never named through one.
    """

    def save_group(self, group: Group) -> None: ...

    def save_membership(self, membership: GroupMembership) -> None: ...

    def find_groups(
        self,
        *,
        name: str | None,
        kind: GroupKind | None,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[Group]: ...

    def list_group_memberships(
        self,
        group_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[GroupMembership]: ...

    def list_person_memberships(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[tuple[GroupMembership, Group]]: ...
