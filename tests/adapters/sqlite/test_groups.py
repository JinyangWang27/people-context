"""SQLite groups and memberships (M28.1) and shared connections (M28.2).

Covers storage, disclosure, lifecycle, portability, and read-time connection derivation.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.db import open_db
from people_context.app.exports.sync_bundle import render_bundle_json
from people_context.app.groups.commands import AddGroupMembershipInput, CloseGroupMembershipInput, CreateGroupInput
from people_context.app.people import RememberPersonInput
from people_context.app.records import CorrectRecordInput
from people_context.app.relationships.commands import SetRelationshipInput
from people_context.domain.group import GroupKind, MembershipRole, TemporalBasis
from people_context.domain.shared import Sensitivity
from people_context.domain.sync_bundle import SYNC_BUNDLE_VERSION, InvalidBundleError, TargetNotEmptyError

_NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)
_GROUP_MIGRATION = 9


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "origin.db", clock=_Clock())
    yield built
    built.close()


def _person(runtime: ApplicationRuntime, name: str) -> str:
    return runtime.use_cases.remember_person.execute(RememberPersonInput(name=name)).person.id


def _group(runtime: ApplicationRuntime, name: str = "Class 1, Grade 6", **fields: Any) -> str:
    fields.setdefault("kind", GroupKind.CLASS)
    return runtime.use_cases.create_group.execute(CreateGroupInput(name=name, **fields)).id


def _member(runtime: ApplicationRuntime, group_id: str, person_id: str, **fields: Any) -> str:
    return runtime.use_cases.add_group_membership.execute(
        AddGroupMembershipInput(group_id=group_id, person_id=person_id, **fields)
    ).id


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed constants


class TestSchema:
    def test_a_fresh_database_creates_both_tables_at_the_group_migration(self) -> None:
        conn = open_db(":memory:")

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        assert {"identified_groups", "group_memberships"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= _GROUP_MIGRATION

    def test_a_legacy_database_upgrades_without_inventing_groups(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.db"
        legacy = open_db(path)
        legacy.execute("DROP TABLE group_memberships")
        legacy.execute("DROP TABLE identified_groups")
        legacy.execute(f"PRAGMA user_version = {_GROUP_MIGRATION - 1}")
        legacy.commit()
        legacy.close()

        upgraded = open_db(path)

        assert upgraded.execute("PRAGMA user_version").fetchone()[0] >= _GROUP_MIGRATION
        assert _count(upgraded, "identified_groups") == 0


class TestReads:
    def test_visible_memberships_survive_hidden_neighbours_without_changing_truncation(
        self, runtime: ApplicationRuntime
    ) -> None:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        group = _group(runtime)
        visible = _member(runtime, group, alice, valid_from=date(2015, 9, 1), role=MembershipRole.STUDENT)
        _member(runtime, group, bob, valid_from=date(2014, 9, 1), sensitivity=Sensitivity.RESTRICTED)

        page = runtime.use_cases.get_group.execute(group, limit=1)

        assert [row.id for row in page.memberships] == [visible]
        assert page.truncated is False
        assert runtime.use_cases.get_group.execute(group, limit=1, include_sensitive=True).truncated is True

    def test_a_visible_membership_in_a_hidden_group_is_withheld(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")
        hidden = _group(runtime, "Support circle", kind=GroupKind.COMMUNITY, sensitivity=Sensitivity.SENSITIVE)
        _member(runtime, hidden, alice)

        assert runtime.use_cases.get_group.execute(hidden).found is False
        assert runtime.use_cases.find_groups.execute(name="support").groups == []
        assert runtime.use_cases.list_person_memberships.execute(alice).memberships == []
        entries = runtime.use_cases.list_person_memberships.execute(alice, include_sensitive=True).memberships
        assert [entry.group.group.name for entry in entries] == ["Support circle"]

    def test_equal_names_stay_distinct_and_order_deterministically(self, runtime: ApplicationRuntime) -> None:
        first, second = _group(runtime, "Class 1"), _group(runtime, "CLASS 1")

        found = runtime.use_cases.find_groups.execute(name="class 1", kind=GroupKind.CLASS)

        assert first != second
        assert [row.group.id for row in found.groups] == sorted([first, second])

    def test_concurrent_and_historical_memberships_are_kept_and_ordered(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")
        team, club = _group(runtime, "Platform", kind=GroupKind.TEAM), _group(runtime, "Chess", kind=GroupKind.CLUB)
        later = _member(runtime, team, alice, valid_from=date(2022, 1, 1), temporal_basis=TemporalBasis.ONGOING)
        earlier = _member(runtime, club, alice, valid_from=date(2010, 1, 1), valid_to=date(2012, 1, 1))
        undated = _member(runtime, club, alice)

        entries = runtime.use_cases.list_person_memberships.execute(alice).memberships

        assert [entry.membership.id for entry in entries] == [earlier, later, undated]

    def test_reads_write_nothing(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")
        group = _group(runtime)
        _member(runtime, group, alice)
        before = (_count(runtime.conn, "audit_log"), _count(runtime.conn, "changelog"))

        runtime.use_cases.find_groups.execute()
        runtime.use_cases.get_group.execute(group, include_sensitive=True)
        runtime.use_cases.list_person_memberships.execute(alice)

        assert (_count(runtime.conn, "audit_log"), _count(runtime.conn, "changelog")) == before


class TestLifecycle:
    def test_closure_is_audited_and_replayable(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")
        membership = _member(runtime, _group(runtime), alice, valid_from=date(2015, 9, 1))

        runtime.use_cases.close_group_membership.execute(
            CloseGroupMembershipInput(membership_id=membership, ended_on=date(2016, 7, 15))
        )

        ops = runtime.conn.execute(
            "SELECT op_kind FROM changelog WHERE entity_id = ? ORDER BY hlc_physical_ms, hlc_logical", (membership,)
        ).fetchall()
        assert [row["op_kind"] for row in ops] == ["create", "update"]

    def test_forgetting_a_person_removes_their_memberships_and_keeps_everyone_elses(
        self, runtime: ApplicationRuntime
    ) -> None:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        group = _group(runtime)
        alice_membership = _member(runtime, group, alice)
        bob_membership = _member(runtime, group, bob)

        preview = runtime.use_cases.preview_forget.execute(alice)
        result = runtime.use_cases.forget.execute(alice, "person")

        assert preview.deleted["group_memberships"] == 1
        assert result.deleted["group_memberships"] == 1
        remaining = runtime.use_cases.get_group.execute(group)
        assert [row.id for row in remaining.memberships] == [bob_membership]
        redacted = runtime.conn.execute(
            "SELECT payload_json FROM changelog WHERE entity_id = ?", (alice_membership,)
        ).fetchone()
        assert json.loads(redacted["payload_json"]) == {"redacted": True}
        group_history = runtime.conn.execute(
            "SELECT payload_json FROM audit_log WHERE entity_id = ?", (group,)
        ).fetchone()
        assert json.loads(group_history["payload_json"])["name"] == "Class 1, Grade 6"

    def test_a_person_in_no_group_forgets_with_an_unchanged_summary(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")

        assert "group_memberships" not in runtime.use_cases.forget.execute(alice, "person").deleted

    def test_forgetting_a_group_erases_its_memberships_and_their_history(self, runtime: ApplicationRuntime) -> None:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        group = _group(runtime, "Support circle")
        memberships = [_member(runtime, group, alice), _member(runtime, group, bob)]

        result = runtime.use_cases.forget.execute(f"group:{group}", "record")

        assert result.deleted == {"identified_groups": 1, "group_memberships": 2}
        assert _count(runtime.conn, "group_memberships") == 0
        for entity_id in [group, *memberships]:
            rows = runtime.conn.execute("SELECT payload_json FROM audit_log WHERE entity_id = ?", (entity_id,))
            assert all(json.loads(row["payload_json"]) == {"redacted": True} for row in rows)

    def test_forgetting_one_membership_leaves_the_group(self, runtime: ApplicationRuntime) -> None:
        alice = _person(runtime, "Alice")
        group = _group(runtime)
        membership = _member(runtime, group, alice)

        result = runtime.use_cases.forget.execute(f"group_membership:{membership}", "record")

        assert result.deleted == {"group_memberships": 1}
        assert runtime.use_cases.get_group.execute(group).found is True

    def test_merge_moves_memberships_without_combining_them(self, runtime: ApplicationRuntime) -> None:
        alice, duplicate = _person(runtime, "Alice"), _person(runtime, "Alice Duplicate")
        group = _group(runtime)
        kept = _member(runtime, group, alice, role=MembershipRole.STUDENT)
        moved = _member(runtime, group, duplicate, role=MembershipRole.STUDENT)

        result = runtime.use_cases.merge_people.execute(alice, duplicate)

        assert result.moved.group_memberships == 1
        entries = runtime.use_cases.list_person_memberships.execute(alice).memberships
        assert sorted(entry.membership.id for entry in entries) == sorted([kept, moved])
        merge_transaction = runtime.conn.execute(
            "SELECT transaction_id FROM changelog WHERE entity_id = ? AND op_kind = 'update'", (moved,)
        ).fetchone()["transaction_id"]
        merge_rows = runtime.conn.execute(
            "SELECT entity_type FROM changelog WHERE transaction_id = ?", (merge_transaction,)
        ).fetchall()
        # The reparenting shares the merge's own transaction with the survivor's update.
        assert {"person", "group_membership"} <= {row["entity_type"] for row in merge_rows}


class TestSharedConnections:
    def _classmates(self, runtime: ApplicationRuntime) -> tuple[str, str, str, str, str]:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        group = _group(runtime)
        period: dict[str, Any] = {"role": MembershipRole.STUDENT, "valid_from": date(2015, 9, 1)}
        first = _member(runtime, group, alice, valid_to=date(2016, 6, 30), **period)
        second = _member(runtime, group, bob, valid_to=date(2016, 7, 15), **period)
        return alice, bob, group, first, second

    def _labels(self, runtime: ApplicationRuntime, a: str, b: str) -> list[str | None]:
        return [row.label for row in runtime.use_cases.explain_shared_connections.execute(a, b).connections]

    def test_the_lookup_writes_nothing_and_leaves_graph_reads_unchanged(self, runtime: ApplicationRuntime) -> None:
        alice, bob, _, first, second = self._classmates(runtime)
        carol = _person(runtime, "Carol")
        for person in (alice, bob):
            runtime.use_cases.set_relationship.execute(
                SetRelationshipInput(subject_id=person, object_id=carol, type="friend_of")
            )
        before = [_count(runtime.conn, table) for table in ("audit_log", "changelog", "relationships")]
        path = runtime.use_cases.find_connection.execute(alice, bob)
        graph = runtime.use_cases.get_relationship_graph.execute(alice)

        document = runtime.use_cases.explain_shared_connections.execute(alice, bob, include_sensitive=True)

        assert [(row.membership_a.id, row.membership_b.id, row.label) for row in document.connections] == [
            (first, second, "classmates")
        ]
        assert document.direct_relationships == []
        assert [_count(runtime.conn, table) for table in ("audit_log", "changelog", "relationships")] == before
        assert runtime.use_cases.find_connection.execute(alice, bob) == path
        assert runtime.use_cases.get_relationship_graph.execute(alice) == graph

    def test_a_direct_relationship_is_reported_separately(self, runtime: ApplicationRuntime) -> None:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        relationship = runtime.use_cases.set_relationship.execute(
            SetRelationshipInput(subject_id=bob, object_id=alice, type="friend_of")
        )

        document = runtime.use_cases.explain_shared_connections.execute(alice, bob)

        assert document.connections == []
        assert [row.id for row in document.direct_relationships] == [relationship.id]

    def test_correction_changes_the_next_lookup(self, runtime: ApplicationRuntime) -> None:
        alice, bob, _, first, _ = self._classmates(runtime)

        runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="group_membership", entity_id=first, fields={"role": "teacher"})
        )

        assert self._labels(runtime, alice, bob) == [None]

    def test_correcting_a_membership_to_sensitive_hides_it_from_ordinary_lookups(
        self, runtime: ApplicationRuntime
    ) -> None:
        alice, bob, _, first, _ = self._classmates(runtime)

        runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="group_membership", entity_id=first, fields={"sensitivity": "sensitive"})
        )

        assert self._labels(runtime, alice, bob) == []
        document = runtime.use_cases.explain_shared_connections.execute(alice, bob, include_sensitive=True)
        assert [row.label for row in document.connections] == ["classmates"]

    def test_closure_before_the_other_start_removes_the_overlap(self, runtime: ApplicationRuntime) -> None:
        alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
        team = _group(runtime, "Platform", kind=GroupKind.TEAM)
        ongoing: dict[str, Any] = {"role": MembershipRole.PARTICIPANT, "temporal_basis": TemporalBasis.ONGOING}
        first = _member(runtime, team, alice, valid_from=date(2020, 1, 1), **ongoing)
        _member(runtime, team, bob, valid_from=date(2024, 1, 1), **ongoing)
        assert self._labels(runtime, alice, bob) == ["teammates"]

        runtime.use_cases.close_group_membership.execute(
            CloseGroupMembershipInput(membership_id=first, ended_on=date(2022, 12, 31))
        )

        [connection] = runtime.use_cases.explain_shared_connections.execute(alice, bob).connections
        assert (connection.label, connection.temporal.value) == (None, "disjoint")

    def test_merge_and_forget_change_the_next_lookup(self, runtime: ApplicationRuntime) -> None:
        alice, bob, group, _, _ = self._classmates(runtime)
        duplicate = _person(runtime, "Bob Duplicate")
        _member(runtime, group, duplicate, role=MembershipRole.TEACHER)

        runtime.use_cases.merge_people.execute(bob, duplicate)

        assert sorted(self._labels(runtime, alice, bob), key=str) == [None, "classmates"]
        assert runtime.use_cases.explain_shared_connections.execute(alice, duplicate).found is False

        runtime.use_cases.forget.execute(bob, "person")

        assert runtime.use_cases.explain_shared_connections.execute(alice, bob).found is False
        assert _count(runtime.conn, "group_memberships") == 1


class TestPortability:
    def _seed(self, runtime: ApplicationRuntime) -> tuple[str, str, str]:
        alice = _person(runtime, "Alice")
        group = _group(runtime, sensitivity=Sensitivity.SENSITIVE)
        membership = _member(
            runtime, group, alice, role=MembershipRole.STUDENT, valid_from=date(2015, 9, 1), stated_by="Alice"
        )
        return alice, group, membership

    def test_export_and_restore_round_trip_groups_and_memberships(
        self, runtime: ApplicationRuntime, tmp_path: Path
    ) -> None:
        _alice, group, membership = self._seed(runtime)
        document = runtime.use_cases.export_sync_bundle.execute()
        assert document.version == SYNC_BUNDLE_VERSION == 5
        assert [row.id for row in document.groups] == [group]
        assert [row.id for row in document.group_memberships] == [membership]

        destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
        try:
            parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
            assert destination.use_cases.restore_sync_bundle.preview(parsed).counts["group memberships"] == 1
            outcome = destination.use_cases.restore_sync_bundle.execute(parsed)
            restored = destination.use_cases.get_group.execute(group, include_sensitive=True)
        finally:
            destination.close()

        assert (outcome.groups, outcome.group_memberships) == (1, 1)
        assert restored.group is not None and restored.group.group.sensitivity is Sensitivity.SENSITIVE
        assert [(row.id, row.provenance.stated_by) for row in restored.memberships] == [(membership, "Alice")]

    def test_the_plain_export_carries_both_collections(self, runtime: ApplicationRuntime) -> None:
        _alice, group, membership = self._seed(runtime)

        document = runtime.use_cases.export_data.execute()

        assert [row["id"] for row in document.groups] == [group]
        assert [row["id"] for row in document.group_memberships] == [membership]

    def test_a_version_four_bundle_restores_with_no_groups(self, runtime: ApplicationRuntime) -> None:
        self._seed(runtime)
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        payload["version"] = 4
        payload.pop("groups")
        payload.pop("group_memberships")

        document = runtime.use_cases.restore_sync_bundle.parse(json.dumps(payload))

        assert (document.groups, document.group_memberships) == ([], [])

    def test_a_version_four_bundle_carrying_groups_is_refused(self, runtime: ApplicationRuntime) -> None:
        self._seed(runtime)
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        payload["version"] = 4

        with pytest.raises(InvalidBundleError):
            runtime.use_cases.restore_sync_bundle.parse(json.dumps(payload))

    @pytest.mark.parametrize(
        ("mutate", "reason"),
        [
            (lambda payload: payload["group_memberships"][0].update(person_id="MISSING"), "unknown person"),
            (lambda payload: payload["group_memberships"][0].update(group_id="MISSING"), "unknown group"),
            (lambda payload: payload["groups"][0].update(organization_id="MISSING"), "unknown organization"),
            (lambda payload: payload["groups"].append(dict(payload["groups"][0])), "duplicate group id"),
            (lambda payload: payload["groups"][0].update(name=" "), "name"),
            (lambda payload: payload["groups"][0].update(name="x" * 257), "name"),
        ],
    )
    def test_inconsistent_group_state_is_refused_without_echoing_names(
        self, runtime: ApplicationRuntime, mutate: Any, reason: str
    ) -> None:
        self._seed(runtime)
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        mutate(payload)

        with pytest.raises(InvalidBundleError) as raised:
            runtime.use_cases.restore_sync_bundle.parse(json.dumps(payload))

        assert any(reason in detail for detail in raised.value.details)
        assert not any("Class 1" in detail for detail in raised.value.details)
        assert _count(runtime.conn, "identified_groups") == 1

    @pytest.mark.parametrize("table", ["identified_groups", "group_memberships"])
    @pytest.mark.parametrize("version", [1, 5])
    def test_a_non_empty_group_table_refuses_restore(
        self, runtime: ApplicationRuntime, tmp_path: Path, table: str, version: int
    ) -> None:
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        if version == 1:
            payload["version"] = 1
            for key in ("groups", "group_memberships", "trait_evidence", "imports"):
                payload.pop(key)
        destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
        try:
            document = destination.use_cases.restore_sync_bundle.parse(json.dumps(payload))
            alice = _person(destination, "Alice")
            group = _group(destination)
            _member(destination, group, alice)
            destination.conn.execute("PRAGMA foreign_keys=OFF")
            for other in ("identified_groups", "group_memberships"):
                if other != table:
                    destination.conn.execute(f"DELETE FROM {other}")  # noqa: S608 - fixed constants
            for cleared in ("persons", "aliases", "person_search", "audit_log", "changelog"):
                destination.conn.execute(f"DELETE FROM {cleared}")  # noqa: S608 - fixed constants
            destination.conn.commit()

            with pytest.raises(TargetNotEmptyError) as raised:
                destination.use_cases.restore_sync_bundle.execute(document)
        finally:
            destination.close()

        assert any(table in detail for detail in raised.value.details)
