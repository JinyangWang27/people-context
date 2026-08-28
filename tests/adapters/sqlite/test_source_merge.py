"""Merge integration for durable candidate mappings and retained staged person matches."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportSourceStore,
    SqliteImportStagingStore,
    SqliteMergeStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.imports import CandidateStager, CommitImport, ReviewImport, StageCandidates
from people_context.app.people import MergePeople, RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        clock = _Clock()
        self.people = SqlitePeopleRepository(conn)
        self.records = SqliteRecordStore(conn)
        self.audit = SqliteAuditLog(conn)
        self.staging = SqliteImportStagingStore(conn)
        self.sources = SqliteImportSourceStore(conn)
        self.stage = StageCandidates(CandidateStager(self.people, self.staging, clock, self.sources, self.audit))
        self.review = ReviewImport(self.staging)
        self.commit = CommitImport(
            self.people,
            self.staging,
            RememberPerson(self.people, self.people, self.audit, clock),
            RecordInteraction(self.people, self.records, self.audit, clock),
            SetAffiliation(self.people, SqliteOrganizationStore(conn), self.records, self.audit, clock),
            RecordFact(self.people, self.records, self.audit, clock),
            RecordObservation(self.people, self.records, self.audit, clock),
            RecordTrait(self.people, self.records, self.audit, clock),
            SetRelationship(
                self.people,
                SqliteRelationshipStore(conn),
                self.audit,
                clock,
                SqliteRelationshipVocabularyStore(conn),
            ),
            self.sources,
            self.audit,
            clock,
        )
        self.merge = MergePeople(self.people, SqliteMergeStore(conn), clock, self.audit)

    def mappings(self) -> dict[str, sqlite3.Row]:
        return {
            row["candidate_id"]: row
            for row in self.conn.execute("SELECT * FROM import_candidate_mappings").fetchall()
        }

    def staged(self, row_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT candidate_json FROM import_staging WHERE id = ?", (row_id,)).fetchone()
        return json.loads(row["candidate_json"])

    def changelog(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM changelog").fetchall()


@pytest.fixture
def harness() -> _Harness:
    return _Harness(open_db(":memory:"))


def _person(ref: str, name: str, email: str) -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": [{"value": email, "kind": "handle"}]}


def _stage_and_commit(harness: _Harness, candidates: list[dict[str, Any]], digest: str = _DIGEST) -> dict[str, str]:
    """Stage and fully commit one tracked batch, returning candidate id by person ref/type."""
    batch = harness.stage.execute("weekly-sync", candidates, source_kind="meeting_transcript", content_digest=digest)
    rows = harness.review.execute(batch.batch_id).candidates
    harness.commit.execute(batch.batch_id, [row.id for row in rows])
    return {row.candidate.get("name", row.candidate["type"]): row.id for row in rows}


def test_a_mapping_to_the_duplicate_person_follows_the_survivor(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]

    harness.merge.execute(primary, duplicate)

    after = harness.mappings()
    assert after[ids["Ally Ahmed"]]["entity_id"] == primary
    assert after[ids["Ally Ahmed"]]["disposition"] == "entity"
    assert after[ids["Alice Ahmed"]]["entity_id"] == primary


def test_a_reparented_record_mapping_keeps_its_entity_id(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Ally Ahmed", "ally@example.com"),
            {"type": "fact", "person_ref": "b", "predicate": "city", "value": "Berlin"},
        ],
    )
    mappings = harness.mappings()
    fact_id = mappings[ids["fact"]]["entity_id"]
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]

    harness.merge.execute(primary, duplicate)

    assert harness.mappings()[ids["fact"]]["entity_id"] == fact_id
    reparented = harness.records.get_record("fact", fact_id)
    assert reparented is not None and reparented.person_id == primary


def test_a_relationship_removed_as_a_self_loop_becomes_a_terminal_outcome(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Ally Ahmed", "ally@example.com"),
            {"type": "relationship", "from_ref": "a", "to_ref": "b", "relationship_type": "colleague of"},
        ],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    edge_id = mappings[ids["relationship"]]["entity_id"]

    harness.merge.execute(primary, duplicate)

    terminal = harness.mappings()[ids["relationship"]]
    assert terminal["disposition"] == "merged_away"
    assert terminal["entity_id"] is None
    assert terminal["entity_type"] == "relationship"
    assert harness.records.get_record("relationship", edge_id) is None


def test_a_terminal_outcome_does_not_recreate_its_removed_edge_on_retry(harness: _Harness) -> None:
    batch = harness.stage.execute(
        "weekly-sync",
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Ally Ahmed", "ally@example.com"),
            {"type": "relationship", "from_ref": "a", "to_ref": "b", "relationship_type": "colleague of"},
        ],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = harness.review.execute(batch.batch_id).candidates
    accepted = [row.id for row in rows]
    harness.commit.execute(batch.batch_id, accepted)
    mappings = harness.mappings()
    relationship_row = next(row for row in rows if row.candidate["type"] == "relationship")
    primary = mappings[next(row.id for row in rows if row.candidate.get("name") == "Alice Ahmed")]["entity_id"]
    duplicate = mappings[next(row.id for row in rows if row.candidate.get("name") == "Ally Ahmed")]["entity_id"]
    harness.merge.execute(primary, duplicate)

    result = harness.commit.execute(batch.batch_id, accepted)

    assert relationship_row.id in result.skipped_ids
    assert harness.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0
    assert harness.mappings()[relationship_row.id]["disposition"] == "merged_away"


def test_a_deduplicated_relationship_mapping_follows_the_keeper(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Ally Ahmed", "ally@example.com"),
            _person("c", "Carla Diaz", "carla@example.com"),
            {"type": "relationship", "from_ref": "a", "to_ref": "c", "relationship_type": "colleague of"},
        ],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    carla = mappings[ids["Carla Diaz"]]["entity_id"]
    # A parallel edge from the duplicate to the same third person, committed separately.
    second = _stage_and_commit(
        harness,
        [
            _person("b2", "Ally Ahmed", "ally@example.com"),
            _person("c2", "Carla Diaz", "carla@example.com"),
            {"type": "relationship", "from_ref": "b2", "to_ref": "c2", "relationship_type": "colleague of"},
        ],
        digest=_OTHER_DIGEST,
    )
    duplicate_edge = harness.mappings()[second["relationship"]]["entity_id"]
    assert duplicate_edge is not None

    harness.merge.execute(primary, duplicate)

    after = harness.mappings()
    surviving = {row["id"] for row in harness.conn.execute("SELECT id FROM relationships")}
    # The merge collapsed the two parallel edges into one keeper.
    assert len(surviving) == 1
    # Both candidates legitimately map to that surviving edge rather than dangling.
    for candidate_id in (ids["relationship"], second["relationship"]):
        outcome = after[candidate_id]
        assert outcome["disposition"] == "entity"
        assert outcome["entity_id"] in surviving
    remaining = harness.records.get_record("relationship", next(iter(surviving)))
    assert remaining is not None
    assert {remaining.subject_id, remaining.object_id} == {primary, carla}
    assert harness.records.get_record("relationship", duplicate_edge) is None or duplicate_edge in surviving


def test_a_retained_person_match_is_retargeted_to_the_survivor(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    # A second, still-incomplete batch whose person candidate matched the duplicate.
    pending = harness.stage.execute(
        "call-note",
        [
            _person("b2", "Ally Ahmed", "ally@example.com"),
            {"type": "fact", "person_ref": "b2", "predicate": "city", "value": "Berlin"},
        ],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    pending_rows = harness.review.execute(pending.batch_id).candidates
    person_row = next(row for row in pending_rows if row.candidate["type"] == "person")
    assert harness.staged(person_row.id)["matched_person_id"] == duplicate

    harness.merge.execute(primary, duplicate)

    assert harness.staged(person_row.id)["matched_person_id"] == primary
    # And the dependent commit that would previously have failed the active-person check works.
    result = harness.commit.execute(pending.batch_id, [row.id for row in pending_rows])
    assert result.unresolved_ids == []
    fact_row = next(row for row in pending_rows if row.candidate["type"] == "fact")
    fact_mapping = harness.mappings()[fact_row.id]
    stored = harness.records.get_record("fact", fact_mapping["entity_id"])
    assert stored is not None and stored.person_id == primary


def test_staging_retarget_mints_no_changelog_row_of_its_own(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    harness.stage.execute(
        "call-note",
        [_person("b2", "Ally Ahmed", "ally@example.com")],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    before = {row["op_id"] for row in harness.changelog()}

    harness.merge.execute(primary, duplicate)

    produced = [row for row in harness.changelog() if row["op_id"] not in before]
    assert produced, "the merge is expected to emit replay history"
    assert "import_staging" not in {row["entity_type"] for row in produced}


def test_mapping_retarget_shares_the_merge_transaction_id(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    before = {row["op_id"] for row in harness.changelog()}

    harness.merge.execute(primary, duplicate)

    produced = [row for row in harness.changelog() if row["op_id"] not in before]
    assert len({row["transaction_id"] for row in produced}) == 1
    assert "import_candidate_mapping" in {row["entity_type"] for row in produced}


def test_a_failed_merge_rolls_back_the_staging_retarget(harness: _Harness) -> None:
    ids = _stage_and_commit(
        harness,
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
    )
    mappings = harness.mappings()
    primary = mappings[ids["Alice Ahmed"]]["entity_id"]
    duplicate = mappings[ids["Ally Ahmed"]]["entity_id"]
    pending = harness.stage.execute(
        "call-note",
        [_person("b2", "Ally Ahmed", "ally@example.com")],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    person_row = harness.review.execute(pending.batch_id).candidates[0]

    def _fail(name: str) -> None:
        if name == "before_audit":
            raise RuntimeError("merge failed after the store writes")

    failing = MergePeople(harness.people, SqliteMergeStore(harness.conn, _fail), _Clock(), harness.audit)
    with pytest.raises(RuntimeError):
        failing.execute(primary, duplicate)

    assert harness.staged(person_row.id)["matched_person_id"] == duplicate
    assert harness.mappings()[ids["Ally Ahmed"]]["entity_id"] == duplicate
