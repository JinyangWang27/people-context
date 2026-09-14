"""MCP tools for identified groups and membership assertions (M28.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from people_context.adapters.mcp.tools.references import resolve_reference
from people_context.adapters.mcp.tools.tool_errors import call_action, flag_refusals
from people_context.app.groups.commands import (
    AddGroupMembershipInput,
    CloseGroupMembershipInput,
    CreateGroupInput,
)
from people_context.app.groups.queries import DEFAULT_GROUP_LIMIT, GroupQueryError
from people_context.domain.group import GroupKind, MembershipRole, TemporalBasis
from people_context.domain.shared import Sensitivity

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from people_context.adapters.runtime import RuntimeUseCases

_READ_ONLY = ToolAnnotations(read_only_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def register(mcp: MCPServer, deps: RuntimeUseCases) -> None:
    """Register group management and ordinary-disclosure group reads."""

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def create_group(
        name: str,
        kind: GroupKind,
        organization_id: str | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Create one identified group: a class, cohort, team, department, club, household, or community.

        Every call creates a new group; an equal name is never treated as the same group. Use
        `find_groups` first and reuse the returned `id` when the user confirms it is the same one.
        `organization_id` must name an existing organization and only places the group under it —
        it creates no affiliation. `sensitivity` defaults to `personal`; a `sensitive` or
        `restricted` group is withheld from ordinary reads together with every membership in it.
        """
        return call_action(
            lambda: deps.create_group.execute(
                CreateGroupInput(
                    name=name,
                    kind=kind,
                    organization_id=organization_id,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def add_group_membership(
        person_id: str,
        group_id: str,
        role: MembershipRole | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        temporal_basis: TemporalBasis | None = None,
        confidence: float | None = None,
        sensitivity: Sensitivity | None = None,
    ) -> dict[str, Any]:
        """Record that an existing person held a role in an existing group.

        Record only what was stated or confirmed. Leave dates out when they are unknown — never
        guess a school year or fill a January 1 date. `temporal_basis` is `unknown` (no dates),
        `period` (at least one known bound; a missing bound means unknown, not open-ended), or
        `ongoing` (asserted current, no end date); it is inferred from the dates when omitted,
        except that `ongoing` must be stated. `role` defaults to `member`. `sensitivity` defaults
        to `personal`. Concurrent and historical memberships are all kept.
        """
        return call_action(
            lambda: deps.add_group_membership.execute(
                AddGroupMembershipInput(
                    person_id=person_id,
                    group_id=group_id,
                    role=role if role is not None else MembershipRole.MEMBER,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    temporal_basis=temporal_basis,
                    confidence=confidence,
                    sensitivity=sensitivity if sensitivity is not None else Sensitivity.PERSONAL,
                )
            )
        )

    @mcp.tool(annotations=_WRITE)
    @flag_refusals
    async def close_group_membership(membership_id: str, ended_on: str) -> dict[str, Any]:
        """Record the last day a historically true membership held.

        Use this when someone left a group; the membership's earlier facts are preserved. It refuses
        a membership that already records an end (`already_ended`) or an end before its start
        (`ends_before_start`). Use `correct_record` with entity type `group_membership` when a
        stored membership was simply wrong.
        """
        return call_action(
            lambda: deps.close_group_membership.execute(
                CloseGroupMembershipInput(membership_id=membership_id, ended_on=ended_on)
            )
        )

    @mcp.tool(annotations=_READ_ONLY)
    @flag_refusals
    async def find_groups(
        name: str | None = None,
        kind: GroupKind | None = None,
        limit: int = DEFAULT_GROUP_LIMIT,
    ) -> dict[str, Any]:
        """List groups whose normalized name contains `name`, optionally of one `kind`.

        Results are candidates, not identity: two groups with the same name stay distinct, so ask
        the user which one is meant when several match. Sensitive and restricted groups are never
        returned by this ordinary tool. Ordered by normalized name then id; `truncated` reports more.
        """
        try:
            return deps.find_groups.execute(name=name, kind=kind, limit=limit).model_dump(mode="json")
        except GroupQueryError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    @flag_refusals
    async def get_group(group_id: str, limit: int = DEFAULT_GROUP_LIMIT) -> dict[str, Any]:
        """Return one group and a bounded page of its recorded memberships.

        These are recorded memberships only: sharing a group is context, not proof that members know
        each other. Sensitive and restricted groups read as `found: false`, and sensitive or
        restricted memberships are omitted without affecting `truncated`.
        """
        try:
            return deps.get_group.execute(group_id, limit=limit).model_dump(mode="json")
        except GroupQueryError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}

    @mcp.tool(annotations=_READ_ONLY)
    @flag_refusals
    async def list_group_memberships(
        person_id: str | None = None,
        limit: int = DEFAULT_GROUP_LIMIT,
        person: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded page of one person's recorded group memberships with each group.

        Pass `person_id` from `resolve_person`, or `person` (a name or alias) to resolve inline.
        Only ordinary memberships in ordinary groups are returned. An unknown or removed person
        returns `found: false`.
        """
        target = resolve_reference(deps, person_id=person_id, person=person)
        if isinstance(target, dict):
            return target
        try:
            return deps.list_person_memberships.execute(target, limit=limit).model_dump(mode="json")
        except GroupQueryError as exc:
            return {"error": "invalid_parameter", "message": str(exc)}
