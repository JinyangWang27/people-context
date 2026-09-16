"""Staged group and membership candidates through the lifecycle (M28.3).

Three things have to hold for the new candidate types to be ordinary staged state rather than a
second class of it: a reviewable batch survives an export and restore intact, a bundle version
that predates them refuses them by name, and hard forget erases a person's placements without
taking the group or anybody else's placements with them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.exports.sync_bundle import render_bundle_json
from people_context.domain.sync_bundle import SYNC_BUNDLE_VERSION, parse_bundle_payload

_NOW = datetime(2026, 9, 14, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "origin.db", clock=_Clock())
    yield built
    built.close()


_BATCH = [
    {"type": "person", "ref": "alice", "name": "Alice Ahmed", "aliases": []},
    {"type": "person", "ref": "bob", "name": "Bob Ali", "aliases": []},
    {"type": "group", "ref": "class-1", "name": "Class 1, Grade 6", "kind": "class"},
    {
        "type": "group_membership",
        "person_ref": "alice",
        "group_ref": "class-1",
        "role": "student",
        "valid_from": "2015-09-01",
        "valid_to": "2016-06-30",
    },
    {"type": "group_membership", "person_ref": "bob", "group_ref": "class-1", "role": "student"},
]


def _stage(runtime: ApplicationRuntime) -> str:
    return runtime.use_cases.stage_candidates.execute(
        "classroom-chat", _BATCH, source_kind="conversation"
    ).batch_id


def _staged(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT id, candidate_json FROM import_staging").fetchall()
    return {row["id"]: json.loads(row["candidate_json"]) for row in rows}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed constants


def test_a_reviewable_batch_of_group_candidates_survives_export_and_restore(
    runtime: ApplicationRuntime, tmp_path: Path
) -> None:
    batch = _stage(runtime)
    document = runtime.use_cases.export_sync_bundle.execute()
    assert document.version == SYNC_BUNDLE_VERSION == 6
    exported = {row.candidate["type"] for row in document.imports.staging}
    assert {"group", "group_membership"} <= exported

    destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
    try:
        parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
        destination.use_cases.restore_sync_bundle.execute(parsed)
        restored = destination.use_cases.review_import.execute(batch).candidates
        # The restored batch is still committable, which is the whole point of carrying it.
        result = destination.use_cases.commit_import.execute(batch, [row.id for row in restored])
        memberships = _count(destination.conn, "group_memberships")
    finally:
        destination.close()

    assert {row.candidate["type"] for row in restored} == {"person", "group", "group_membership"}
    assert result.unresolved_ids == []
    assert memberships == 2


def test_a_group_reference_survives_the_round_trip_pointing_at_the_same_row(runtime: ApplicationRuntime) -> None:
    _stage(runtime)
    document = runtime.use_cases.export_sync_bundle.execute()

    rows = {row.id: row.candidate for row in document.imports.staging}
    group_ids = [row_id for row_id, candidate in rows.items() if candidate["type"] == "group"]
    referenced = {
        candidate["group_candidate_id"] for candidate in rows.values() if candidate["type"] == "group_membership"
    }
    assert referenced == set(group_ids)


@pytest.mark.parametrize("version", [2, 3, 4, 5])
@pytest.mark.parametrize("candidate_type", ["group", "group_membership"])
def test_a_document_predating_m28_3_refuses_the_new_candidate_types(
    runtime: ApplicationRuntime, version: int, candidate_type: str
) -> None:
    """A released version is a closed shape, and this one cannot even fail closed on a field.

    The discriminator picks the model before any field is inspected, so a reader that did not
    refuse the type by name would restore a membership it has no group reference namespace to
    resolve and no group commit pass to run — a pending row review lists and commit never
    resolves, while the receipt's claim keeps suppressing a corrected restage.
    """
    _stage(runtime)
    payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
    payload["version"] = version
    payload["imports"]["staging"] = [
        row for row in payload["imports"]["staging"] if row["candidate"]["type"] in {"person", candidate_type}
    ]

    with pytest.raises(ValidationError):
        parse_bundle_payload(payload)


def test_forgetting_a_person_erases_their_placements_and_leaves_everyone_else_theirs(
    runtime: ApplicationRuntime,
) -> None:
    batch = _stage(runtime)
    rows = runtime.use_cases.review_import.execute(batch).candidates
    people = {row.id: row.candidate["name"] for row in rows if row.candidate["type"] == "person"}
    runtime.use_cases.commit_import.execute(batch, [row.id for row in rows if row.candidate["type"] == "person"])
    alice = next(
        row["entity_id"]
        for row in runtime.conn.execute("SELECT candidate_id, entity_id FROM import_candidate_mappings").fetchall()
        if people.get(row["candidate_id"]) == "Alice Ahmed"
    )

    runtime.use_cases.forget.execute(alice, "person")

    remaining = _staged(runtime.conn)
    assert sorted(candidate["type"] for candidate in remaining.values()) == ["group", "group_membership", "person"]
    # The group candidate outlives the person: it is not about her, and nobody else's placement
    # in it should vanish because one member asked to be erased.
    assert [candidate["name"] for candidate in remaining.values() if candidate["type"] == "group"] == [
        "Class 1, Grade 6"
    ]
    assert [candidate["name"] for candidate in remaining.values() if candidate["type"] == "person"] == ["Bob Ali"]


def _partially_commit(runtime: ApplicationRuntime) -> str:
    """Commit the people, the group, and one of the two memberships, leaving the other pending."""
    batch = _stage(runtime)
    rows = runtime.use_cases.review_import.execute(batch).candidates
    accepted = [row.id for row in rows if row.candidate["type"] in {"person", "group"}]
    accepted += [row.id for row in rows if row.candidate["type"] == "group_membership"][:1]
    runtime.use_cases.commit_import.execute(batch, accepted)
    return batch


def test_a_partially_committed_batch_round_trips_with_its_outcome_mappings(
    runtime: ApplicationRuntime, tmp_path: Path
) -> None:
    """A reviewable session exports its committed rows too, so their mappings travel beside them.

    Restore checks a mapping's entity type against its candidate's type, which is why the
    candidate is spelled `group_membership` rather than `membership`: the two must agree, or the
    exported bundle is refused as claiming an entity type for the wrong kind of candidate.
    """
    batch = _partially_commit(runtime)
    document = runtime.use_cases.export_sync_bundle.execute()
    mapped = {row.entity_type for row in document.imports.candidate_mappings}
    assert {"group", "group_membership"} <= mapped

    destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
    try:
        parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
        destination.use_cases.restore_sync_bundle.execute(parsed)
        rows = destination.use_cases.review_import.execute(batch).candidates
        pending = [row.id for row in rows if row.status != "committed"]
        # The batch is still finishable on the far side, resolving its group through the mapping.
        result = destination.use_cases.commit_import.execute(batch, pending)
        memberships = _count(destination.conn, "group_memberships")
    finally:
        destination.close()

    assert result.unresolved_ids == []
    assert memberships == 2


def test_forgetting_an_imported_group_takes_its_memberships_mappings_with_it(
    runtime: ApplicationRuntime, tmp_path: Path
) -> None:
    """Memberships cascade with their group, so the outcomes naming them must go too.

    A surviving mapping would keep naming a deleted membership through `source show`, and the
    next bundle would be refused because restore cannot find the entity the mapping points at.
    """
    batch = _stage(runtime)
    rows = runtime.use_cases.review_import.execute(batch).candidates
    runtime.use_cases.commit_import.execute(batch, [row.id for row in rows])
    group_id = next(
        row["entity_id"]
        for row in runtime.conn.execute("SELECT entity_type, entity_id FROM import_candidate_mappings").fetchall()
        if row["entity_type"] == "group"
    )

    runtime.use_cases.forget.execute(f"group:{group_id}", "record")

    remaining = {
        row["entity_type"]
        for row in runtime.conn.execute("SELECT DISTINCT entity_type FROM import_candidate_mappings").fetchall()
    }
    assert remaining == {"person"}
    assert _count(runtime.conn, "group_memberships") == 0

    # And the store still exports a bundle that restores.
    document = runtime.use_cases.export_sync_bundle.execute()
    destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
    try:
        parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
        destination.use_cases.restore_sync_bundle.execute(parsed)
    finally:
        destination.close()
