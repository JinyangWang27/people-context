"""Staging group and membership candidates (M28.3).

What is checked here is the half of the contract that happens before anything durable is
written: the caller's batch-local labels become canonical ids, a batch whose references cannot
be rewritten deterministically is refused whole, and the dates a source did not give stay
absent instead of becoming an assertion about when two people were in the same room.

Group identity is the load-bearing rule. M28.1 offers no get-or-create by name, and staging must
not quietly reintroduce one: a candidate names an existing group through `group_id` or it names
none at all, whatever the name happens to match.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.adapters.sqlite.group_store import SqliteGroupStore
from people_context.app.groups.commands import CreateGroup, CreateGroupInput
from people_context.app.imports import CandidateStager, ImportPipelineError, ReviewImport, StageCandidates
from people_context.domain.group import TemporalBasis

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _use_cases(conn):
    people = SqlitePeopleRepository(conn)
    staging_store = SqliteImportStagingStore(conn)
    stage = StageCandidates(CandidateStager(people, staging_store, _Clock()))
    return stage, ReviewImport(staging_store)


def _person(ref: str, name: str) -> dict:
    return {"type": "person", "ref": ref, "name": name, "aliases": []}


def _group(ref: str = "class-1", **overrides) -> dict:
    return {"type": "group", "ref": ref, "name": "Class 1, Grade 6", "kind": "class", **overrides}


def _membership(person_ref: str = "alice", group_ref: str = "class-1", **overrides) -> dict:
    return {"type": "membership", "person_ref": person_ref, "group_ref": group_ref, **overrides}


def _candidates(review: ReviewImport, batch_id: str) -> dict[str, dict]:
    return {row.candidate["type"]: row.candidate for row in review.execute(batch_id).candidates}


def test_batch_local_refs_become_canonical_candidate_ids() -> None:
    conn = open_db(":memory:")
    stage, review = _use_cases(conn)

    batch = stage.execute("notes", [_person("alice", "Alice"), _group(), _membership(role="student")])
    rows = review.execute(batch.batch_id).candidates

    person_row = next(row for row in rows if row.candidate["type"] == "person")
    group_row = next(row for row in rows if row.candidate["type"] == "group")
    membership = next(row for row in rows if row.candidate["type"] == "membership").candidate

    # The caller's own labels never reach storage: review and commit read canonical ids only.
    assert "ref" not in group_row.candidate
    assert membership["person_candidate_id"] == person_row.id
    assert membership["group_candidate_id"] == group_row.id
    assert "person_ref" not in membership and "group_ref" not in membership


def test_a_group_ref_and_a_person_ref_are_separate_namespaces() -> None:
    """One label may name both, exactly as an `evidence_ref` may repeat a person `ref`."""
    conn = open_db(":memory:")
    stage, review = _use_cases(conn)

    batch = stage.execute("notes", [_person("six-b", "Alice"), _group("six-b"), _membership("six-b", "six-b")])
    rows = review.execute(batch.batch_id).candidates

    membership = next(row for row in rows if row.candidate["type"] == "membership").candidate
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    group_row = next(row for row in rows if row.candidate["type"] == "group")
    assert membership["person_candidate_id"] == person_row.id
    assert membership["group_candidate_id"] == group_row.id


def test_a_name_that_matches_an_existing_group_resolves_to_nothing() -> None:
    """The rule M28.1 states: equal names are lookup candidates, never identity proof."""
    conn = open_db(":memory:")
    existing = CreateGroup(
        SqliteGroupStore(conn), SqliteOrganizationStore(conn), SqliteAuditLog(conn), _Clock()
    ).execute(CreateGroupInput(name="Class 1, Grade 6", kind="class"))
    stage, review = _use_cases(conn)

    batch = stage.execute("notes", [_group()])

    staged = _candidates(review, batch.batch_id)["group"]
    assert "group_id" not in staged
    assert existing.id not in json.dumps(staged)


def test_an_explicit_group_id_is_carried_through_untouched() -> None:
    conn = open_db(":memory:")
    stage, review = _use_cases(conn)

    batch = stage.execute("notes", [_group(group_id="grp-1")])

    assert _candidates(review, batch.batch_id)["group"]["group_id"] == "grp-1"


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        ({}, TemporalBasis.UNKNOWN),
        ({"valid_from": "2015-09-01"}, TemporalBasis.PERIOD),
        ({"valid_to": "2016-06-30"}, TemporalBasis.PERIOD),
        ({"valid_from": "2015-09-01", "valid_to": "2016-06-30"}, TemporalBasis.PERIOD),
        ({"temporal_basis": "ongoing"}, TemporalBasis.ONGOING),
    ],
)
def test_the_staged_basis_is_the_one_the_durable_write_would_have_chosen(dates: dict, expected: TemporalBasis) -> None:
    """Absent dates stay absent and read as unknown; `ongoing` is only ever explicit."""
    conn = open_db(":memory:")
    stage, review = _use_cases(conn)

    batch = stage.execute("notes", [_person("alice", "Alice"), _group(), _membership(**dates)])

    staged = _candidates(review, batch.batch_id)["membership"]
    assert staged["temporal_basis"] == expected.value
    if not dates.get("valid_from"):
        assert "valid_from" not in staged
    if not dates.get("valid_to"):
        assert "valid_to" not in staged


def test_a_membership_naming_an_undeclared_group_is_refused_whole() -> None:
    conn = open_db(":memory:")
    stage, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as raised:
        stage.execute("notes", [_person("alice", "Alice"), _membership(group_ref="never-declared")])

    assert raised.value.code == "invalid_candidates"
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_a_duplicate_group_ref_is_refused_whole() -> None:
    conn = open_db(":memory:")
    stage, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as raised:
        stage.execute("notes", [_group("six-b"), _group("six-b", name="Class 2, Grade 6")])

    assert raised.value.code == "invalid_candidates"
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


@pytest.mark.parametrize(
    "candidates",
    [
        [_group("secret-label-alice-was-bullied")],
        [_person("alice", "Alice"), _membership(group_ref="secret-label-alice-was-bullied")],
    ],
)
def test_a_refused_reference_is_never_echoed_back(candidates: list[dict]) -> None:
    """A `ref` is free-form agent text, so it can carry source wording like any other string."""
    conn = open_db(":memory:")
    stage, _ = _use_cases(conn)
    duplicated = [*candidates, *candidates] if candidates[-1]["type"] == "group" else candidates

    with pytest.raises(ImportPipelineError) as raised:
        stage.execute("notes", duplicated)

    reported = json.dumps({"message": str(raised.value), "details": raised.value.details})
    assert "secret-label-alice-was-bullied" not in reported


def test_a_membership_contradicting_its_own_dates_is_refused() -> None:
    conn = open_db(":memory:")
    stage, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError):
        stage.execute(
            "notes",
            [_person("alice", "Alice"), _group(), _membership(temporal_basis="unknown", valid_from="2015-09-01")],
        )


def test_no_raw_source_field_survives_onto_a_staged_row() -> None:
    conn = open_db(":memory:")
    stage, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as raised:
        stage.execute("notes", [_group(transcript="Alice said they were all in 6B together")])

    assert raised.value.code == "invalid_candidates"
