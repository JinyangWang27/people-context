"""Committing accepted group and membership candidates (M28.3).

Commit is where the two rules that matter become visible. Groups are written before the
memberships that name them, and a membership whose group did not commit is reported unresolved
rather than raised on — so a batch accepted in parts stays committable, and the second pass
resolves its group through the stored mapping the first pass wrote.

Everything runs through the real composition root, because the point of the milestone is that an
imported membership is indistinguishable from a recorded one: the same `CreateGroup` and
`AddGroupMembership` use cases, the same validation, provenance, audit, and changelog seam.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.groups.commands import CreateGroupInput
from people_context.app.groups.connections import ConnectionKind, TemporalOverlap
from people_context.domain.group import GroupKind, TemporalBasis
from people_context.domain.shared import Sensitivity

_NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
_VISIBLE = (Sensitivity.PUBLIC, Sensitivity.PERSONAL)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "origin.db", clock=_Clock())
    yield built
    built.close()


def _person(ref: str, name: str) -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": []}


def _group(ref: str = "class-1", **overrides: Any) -> dict[str, Any]:
    return {"type": "group", "ref": ref, "name": "Class 1, Grade 6", "kind": "class", **overrides}


def _membership(person_ref: str, group_ref: str = "class-1", **overrides: Any) -> dict[str, Any]:
    return {
        "type": "group_membership",
        "person_ref": person_ref,
        "group_ref": group_ref,
        "role": "student",
        **overrides,
    }


_TERM = {"valid_from": "2015-09-01", "valid_to": "2016-06-30"}

_CLASSMATES = [
    _person("alice", "Alice Ahmed"),
    _person("bob", "Bob Ali"),
    _group(),
    _membership("alice", **_TERM),
    _membership("bob", **_TERM),
]


def _stage(runtime: ApplicationRuntime, candidates: list[dict[str, Any]]) -> str:
    return runtime.use_cases.stage_candidates.execute(
        "classroom-chat", candidates, source_kind="conversation"
    ).batch_id


def _rows(runtime: ApplicationRuntime, batch_id: str) -> list[Any]:
    return runtime.use_cases.review_import.execute(batch_id).candidates


def _ids(runtime: ApplicationRuntime, batch_id: str, *types: str) -> list[str]:
    return [row.id for row in _rows(runtime, batch_id) if row.candidate["type"] in types]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed constants


def _only_group(runtime: ApplicationRuntime) -> Any:
    found = runtime.use_cases.find_groups.execute(name="Class 1", kind=None, limit=10)
    assert len(found.groups) == 1
    return found.groups[0].group


def _people(runtime: ApplicationRuntime) -> dict[str, str]:
    rows = runtime.conn.execute("SELECT id, canonical_name FROM persons WHERE deleted_at IS NULL").fetchall()
    return {row["canonical_name"]: row["id"] for row in rows}


def test_a_group_and_its_memberships_commit_into_ordinary_records(runtime: ApplicationRuntime) -> None:
    batch = _stage(runtime, _CLASSMATES)

    result = runtime.use_cases.commit_import.execute(batch, _ids(runtime, batch, "person", "group", "group_membership"))

    assert result.unresolved_ids == []
    group = _only_group(runtime)
    assert group.name == "Class 1, Grade 6"
    assert group.kind is GroupKind.CLASS
    detail = runtime.use_cases.get_group.execute(group.id, limit=10)
    assert len(detail.memberships) == 2
    assert {membership.temporal_basis for membership in detail.memberships} == {TemporalBasis.PERIOD}
    # Provenance says the import wrote it, exactly as a direct write says who wrote that.
    assert {membership.provenance.source for membership in detail.memberships} == {"import/agent:classroom-chat"}


def test_a_committed_membership_is_what_the_shared_lookup_then_explains(runtime: ApplicationRuntime) -> None:
    """The end the milestone exists for: captured context answers the connection question."""
    batch = _stage(runtime, _CLASSMATES)
    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    people = _people(runtime)
    document = runtime.use_cases.explain_shared_connections.execute(people["Alice Ahmed"], people["Bob Ali"])

    assert document.found
    connection = document.connections[0]
    assert connection.connection is ConnectionKind.DERIVED_RELATION
    assert connection.label == "classmates"
    assert connection.temporal is TemporalOverlap.OVERLAP


def test_unknown_dates_support_shared_context_and_never_a_classmate_label(runtime: ApplicationRuntime) -> None:
    batch = _stage(
        runtime,
        [
            _person("alice", "Alice Ahmed"),
            _person("bob", "Bob Ali"),
            _group(),
            _membership("alice"),
            _membership("bob"),
        ],
    )
    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    people = _people(runtime)
    connection = runtime.use_cases.explain_shared_connections.execute(
        people["Alice Ahmed"], people["Bob Ali"]
    ).connections[0]

    assert connection.connection is ConnectionKind.SHARED_CONTEXT
    assert connection.label is None
    assert connection.temporal is TemporalOverlap.UNKNOWN
    assert connection.overlap is None


def test_a_membership_whose_group_was_not_accepted_is_unresolved_not_failed(runtime: ApplicationRuntime) -> None:
    batch = _stage(runtime, _CLASSMATES)

    result = runtime.use_cases.commit_import.execute(batch, _ids(runtime, batch, "person", "group_membership"))

    assert result.committed_ids == _ids(runtime, batch, "person")
    assert result.unresolved_ids == _ids(runtime, batch, "group_membership")
    assert _count(runtime.conn, "group_memberships") == 0


def test_a_later_pass_resolves_its_group_through_the_stored_mapping(runtime: ApplicationRuntime) -> None:
    """A batch accepted in parts: the groups today, the placements once the dates are confirmed."""
    batch = _stage(runtime, _CLASSMATES)
    runtime.use_cases.commit_import.execute(batch, _ids(runtime, batch, "person", "group"))

    second = runtime.use_cases.commit_import.execute(batch, _ids(runtime, batch, "group_membership"))

    assert second.unresolved_ids == []
    assert _count(runtime.conn, "group_memberships") == 2
    assert _count(runtime.conn, "identified_groups") == 1


def test_an_explicit_group_id_records_into_that_group_instead_of_creating_one(runtime: ApplicationRuntime) -> None:
    existing = runtime.use_cases.create_group.execute(
        CreateGroupInput(name="Class 1, Grade 6", kind=GroupKind.CLASS)
    )
    batch = _stage(runtime, [_person("alice", "Alice Ahmed"), _group(group_id=existing.id), _membership("alice")])

    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    assert _count(runtime.conn, "identified_groups") == 1
    assert len(runtime.use_cases.get_group.execute(existing.id, limit=10).memberships) == 1


def test_a_group_id_naming_nothing_leaves_the_batch_unresolved(runtime: ApplicationRuntime) -> None:
    """M28 offers no group merge, so a wrong reuse is not an outcome a commit may guess at."""
    batch = _stage(runtime, [_person("alice", "Alice Ahmed"), _group(group_id="grp-gone"), _membership("alice")])

    result = runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    assert result.unresolved_ids == _ids(runtime, batch, "group", "group_membership")
    assert _count(runtime.conn, "identified_groups") == 0


def test_every_committed_group_write_shares_one_transaction_and_reaches_the_changelog(
    runtime: ApplicationRuntime,
) -> None:
    batch = _stage(runtime, _CLASSMATES)

    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    audited = runtime.conn.execute(
        "SELECT entity_type FROM audit_log WHERE entity_type IN ('group', 'group_membership')"
    ).fetchall()
    assert sorted(row["entity_type"] for row in audited) == ["group", "group_membership", "group_membership"]
    # One commit is one logical transaction, so a peer replaying it sees one atomic unit rather
    # than three writes that happen to be adjacent in time.
    logged = runtime.conn.execute(
        "SELECT entity_type, transaction_id FROM changelog WHERE entity_type IN ('group', 'group_membership')"
    ).fetchall()
    assert sorted(row["entity_type"] for row in logged) == ["group", "group_membership", "group_membership"]
    assert len({row["transaction_id"] for row in logged}) == 1


def test_committed_candidates_carry_mappings_naming_what_they_produced(runtime: ApplicationRuntime) -> None:
    batch = _stage(runtime, _CLASSMATES)

    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    counts = {
        row["entity_type"]: row["n"]
        for row in runtime.conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM import_candidate_mappings GROUP BY entity_type"
        ).fetchall()
    }
    assert counts["group"] == 1
    assert counts["group_membership"] == 2


def test_recommitting_an_already_committed_batch_writes_nothing_twice(runtime: ApplicationRuntime) -> None:
    batch = _stage(runtime, _CLASSMATES)
    accepted = [row.id for row in _rows(runtime, batch)]
    runtime.use_cases.commit_import.execute(batch, accepted)

    again = runtime.use_cases.commit_import.execute(batch, accepted)

    assert again.committed_ids == []
    assert sorted(again.skipped_ids) == sorted(accepted)
    assert _count(runtime.conn, "identified_groups") == 1
    assert _count(runtime.conn, "group_memberships") == 2


def test_nothing_the_source_did_not_say_reaches_a_durable_row(runtime: ApplicationRuntime) -> None:
    """No guessed grade, no filled-in year, no roster the confirmation did not cover."""
    batch = _stage(runtime, [_person("alice", "Alice Ahmed"), _group(), _membership("alice")])
    runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    group = _only_group(runtime)
    membership = runtime.use_cases.get_group.execute(group.id, limit=10).memberships[0]

    assert group.organization_id is None
    assert group.sensitivity in _VISIBLE
    assert membership.temporal_basis is TemporalBasis.UNKNOWN
    assert membership.period.valid_from is None and membership.period.valid_to is None


def test_an_organization_that_no_longer_resolves_leaves_the_group_unresolved(
    runtime: ApplicationRuntime,
) -> None:
    """`CreateGroup` refuses it, and letting that refusal escape would abort the whole commit.

    `commit_import` turns only `ImportPipelineError` into a result, so an uncaught
    `OrganizationNotFoundError` reaches the MCP caller as a tool failure and leaves an immutable
    batch that can never be committed. Unresolved is the answer every other dependency gives.
    """
    batch = _stage(
        runtime,
        [_person("alice", "Alice Ahmed"), _group(organization_id="org-gone"), _membership("alice")],
    )

    result = runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    assert result.unresolved_ids == _ids(runtime, batch, "group", "group_membership")
    assert _count(runtime.conn, "identified_groups") == 0
    # The people still committed: one unresolvable organization is not a failed batch.
    assert result.committed_ids == _ids(runtime, batch, "person")


def test_a_group_id_carrying_whitespace_still_finds_its_group(runtime: ApplicationRuntime) -> None:
    """Whatever `find_groups` returned is what commit must match against, character for character."""
    existing = runtime.use_cases.create_group.execute(
        CreateGroupInput(name="Class 1, Grade 6", kind=GroupKind.CLASS)
    )
    padded = f" {existing.id} "
    batch = _stage(runtime, [_person("alice", "Alice Ahmed"), _group(group_id=padded), _membership("alice")])

    result = runtime.use_cases.commit_import.execute(batch, [row.id for row in _rows(runtime, batch)])

    # The padded id names no row, so the candidate declines rather than creating a second group.
    assert result.unresolved_ids == _ids(runtime, batch, "group", "group_membership")
    assert _count(runtime.conn, "identified_groups") == 1
