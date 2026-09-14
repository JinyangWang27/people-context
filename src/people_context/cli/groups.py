"""`pctx group`: manage identified groups and memberships (M28.1) and explain shared connections (M28.2)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from people_context.adapters.runtime import ApplicationRuntime
from people_context.app._mutation import (
    InvalidCorrectionError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
)
from people_context.app.exports._document import render_json_document
from people_context.app.groups.commands import (
    AddGroupMembershipInput,
    CloseGroupMembershipInput,
    CreateGroupInput,
    InvalidMembershipClosureError,
)
from people_context.app.groups.queries import GroupQueryError, GroupSummary
from people_context.app.records import CorrectRecordInput
from people_context.cli.people import resolve_person
from people_context.cli.rendering import print_table
from people_context.domain.group import GroupKind, GroupMembership

_SOURCE = "cli/group"

GROUP_SENSITIVE_WARNING = (
    "This output includes sensitive and restricted groups or memberships, which the MCP server never "
    "discloses. It is printed to this terminal only; redirecting or sharing it is your own disclosure decision."
)

#: The CLI names a membership `membership`; the stored entity type is `group_membership`.
_CORRECTION_ENTITY_TYPES = {"group": "group", "membership": "group_membership"}


def cmd_group(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch one `group` subcommand."""
    return _GROUP_SUBCOMMANDS[args.group_command](runtime, args)


def cmd_group_create(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    return _write(
        args,
        lambda: runtime.use_cases.create_group.execute(
            CreateGroupInput(
                name=args.name,
                kind=args.kind,
                organization_id=args.organization,
                sensitivity=args.sensitivity,
                source=_SOURCE,
            )
        ),
        "Created group",
    )


def cmd_group_add_member(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    return _write(
        args,
        lambda: runtime.use_cases.add_group_membership.execute(
            AddGroupMembershipInput(
                person_id=person.id,
                group_id=args.group_id,
                role=args.role,
                valid_from=args.valid_from,
                valid_to=args.valid_to,
                temporal_basis=args.basis,
                confidence=args.confidence,
                sensitivity=args.sensitivity,
                stated_by=args.stated_by,
                source=_SOURCE,
            )
        ),
        "Added membership",
    )


def cmd_group_close_member(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    return _write(
        args,
        lambda: runtime.use_cases.close_group_membership.execute(
            CloseGroupMembershipInput(membership_id=args.membership_id, ended_on=args.ended_on, source=_SOURCE)
        ),
        "Closed membership",
    )


def cmd_group_correct(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    fields: dict[str, Any] = {}
    for assignment in args.set:
        name, separator, value = assignment.partition("=")
        if not separator or not name:
            print(f"Error: --set expects FIELD=VALUE, got {assignment!r}", file=sys.stderr)
            return 2
        # An empty value clears an optional field, such as an erroneous end date.
        fields[name] = value or None
    return _write(
        args,
        lambda: runtime.use_cases.correct_record.execute(
            CorrectRecordInput(
                entity_type=_CORRECTION_ENTITY_TYPES[args.entity],
                entity_id=args.entity_id,
                fields=fields,
                source=_SOURCE,
            )
        ),
        "Corrected",
    )


def cmd_group_find(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        document = runtime.use_cases.find_groups.execute(
            name=args.name,
            kind=GroupKind(args.kind) if args.kind else None,
            limit=args.limit,
            include_sensitive=args.include_sensitive,
        )
    except GroupQueryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _warn_if_sensitive(args)
    if args.json:
        print(render_json_document(document), end="")
        return 0
    if not document.groups:
        print("No groups found.")
        return 0
    print_table(["ID", "NAME", "KIND", "ORGANIZATION", "SENSITIVITY"], [_group_row(row) for row in document.groups])
    _more(document.truncated, "groups")
    return 0


def cmd_group_show(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        document = runtime.use_cases.get_group.execute(
            args.group_id, limit=args.limit, include_sensitive=args.include_sensitive
        )
    except GroupQueryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if document.group is None:
        print(f"No group found with id '{args.group_id}'.", file=sys.stderr)
        return 1
    _warn_if_sensitive(args)
    if args.json:
        print(render_json_document(document), end="")
        return 0
    print_table(["ID", "NAME", "KIND", "ORGANIZATION", "SENSITIVITY"], [_group_row(document.group)])
    print()
    if not document.memberships:
        print("No memberships.")
        return 0
    print_table(
        ["MEMBERSHIP", "PERSON", "ROLE", "FROM", "TO", "BASIS", "SENSITIVITY"],
        [(row.id, row.person_id, *_membership_cells(row)) for row in document.memberships],
    )
    _more(document.truncated, "memberships")
    return 0


def cmd_group_memberships(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    person, exit_code = resolve_person(runtime, args.person)
    if person is None:
        return exit_code
    try:
        document = runtime.use_cases.list_person_memberships.execute(
            person.id, limit=args.limit, include_sensitive=args.include_sensitive
        )
    except GroupQueryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not document.found:
        print(f"No person found matching '{args.person}'.", file=sys.stderr)
        return 1
    _warn_if_sensitive(args)
    if args.json:
        print(render_json_document(document), end="")
        return 0
    print(f"{person.canonical_name} ({person.id})")
    if not document.memberships:
        print("No memberships.")
        return 0
    print_table(
        ["MEMBERSHIP", "GROUP", "ROLE", "FROM", "TO", "BASIS", "SENSITIVITY"],
        [
            (entry.membership.id, entry.group.group.name, *_membership_cells(entry.membership))
            for entry in document.memberships
        ],
    )
    _more(document.truncated, "memberships")
    return 0


def cmd_group_shared(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    first, exit_code = resolve_person(runtime, args.person_a)
    if first is None:
        return exit_code
    second, exit_code = resolve_person(runtime, args.person_b)
    if second is None:
        return exit_code
    try:
        document = runtime.use_cases.explain_shared_connections.execute(
            first.id, second.id, limit=args.limit, include_sensitive=args.include_sensitive
        )
    except GroupQueryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _warn_if_sensitive(args)
    if args.json:
        print(render_json_document(document), end="")
        return 0
    print(f"{first.canonical_name} ({first.id}) and {second.canonical_name} ({second.id})")
    if document.connections:
        print_table(
            ["GROUP", "CONNECTION", "LABEL", "TEMPORAL", "OVERLAP", "A MEMBERSHIP", "B MEMBERSHIP"],
            [
                (
                    row.group.group.name,
                    row.connection.value,
                    row.label or "",
                    row.temporal.value,
                    f"{row.overlap.valid_from}..{row.overlap.valid_to}" if row.overlap else "",
                    row.membership_a.id,
                    row.membership_b.id,
                )
                for row in document.connections
            ],
        )
    else:
        print("No shared groups found. This does not mean they do not know each other.")
    _more(document.truncated, "connections")
    if document.memberships_truncated:
        print("\nOne of them has more memberships than one lookup reads; the result may be partial.")
    if document.direct_relationships:
        print("\nDirect relationships:")
        print_table(
            ["ID", "SUBJECT", "TYPE", "OBJECT"],
            [(row.id, row.subject_id, row.type, row.object_id) for row in document.direct_relationships],
        )
    _more(document.direct_relationships_truncated, "direct relationships")
    return 0


def _write(args: argparse.Namespace, action: Callable[[], BaseModel], verb: str) -> int:
    """Run one write and report it; not-found exits 1 and invalid input exits 2."""
    try:
        result = action()
    except (PersonNotFoundError, OrganizationNotFoundError, RecordNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (InvalidCorrectionError, InvalidMembershipClosureError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        # Messages only: a rejected input echoes the submitted values, which may be personal.
        reasons = "; ".join(str(error["msg"]) for error in exc.errors(include_url=False, include_context=False))
        print(f"Error: {reasons}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        print(f"{verb} {getattr(result, 'id', '')}")
    return 0


def _warn_if_sensitive(args: argparse.Namespace) -> None:
    # stderr, so a redirected `--json` document stays byte-identical to the rendered one.
    if args.include_sensitive:
        print(f"Warning: {GROUP_SENSITIVE_WARNING}", file=sys.stderr)


def _group_row(summary: GroupSummary) -> tuple[str, str, str, str, str]:
    group = summary.group
    return (group.id, group.name, group.kind.value, summary.organization_name or "", group.sensitivity.value)


def _membership_cells(membership: GroupMembership) -> tuple[str, str, str, str, str]:
    period = membership.period
    return (
        membership.role.value,
        period.valid_from.isoformat() if period.valid_from else "",
        period.valid_to.isoformat() if period.valid_to else "",
        membership.temporal_basis.value,
        membership.sensitivity.value,
    )


def _more(truncated: bool, subject: str) -> None:
    if truncated:
        print(f"\nMore {subject} exist; raise --limit to see them.")


_GROUP_SUBCOMMANDS: dict[str, Callable[[ApplicationRuntime, argparse.Namespace], int]] = {
    "create": cmd_group_create,
    "find": cmd_group_find,
    "show": cmd_group_show,
    "add-member": cmd_group_add_member,
    "close-member": cmd_group_close_member,
    "correct": cmd_group_correct,
    "memberships": cmd_group_memberships,
    "shared": cmd_group_shared,
}
