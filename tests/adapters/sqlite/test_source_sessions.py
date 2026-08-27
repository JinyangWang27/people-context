"""Source claims, candidate commit mappings, and one transaction id per import commit."""

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
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportPipelineError,
    ReviewImport,
    StageCandidates,
)
from people_context.app.people import RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.ports.sources import (
    STATUS_COMMITTED,
    STATUS_PARTIALLY_COMMITTED,
    STATUS_STAGED,
)

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64
_FINGERPRINT = "b" * 64


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    """One in-memory database wired the way the runtime wires source-tracked imports."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.people = SqlitePeopleRepository(conn)
        self.records = SqliteRecordStore(conn)
        self.audit = SqliteAuditLog(conn)
        self.staging = SqliteImportStagingStore(conn)
        self.sources = SqliteImportSourceStore(conn)
        clock = _Clock()
        self.stager = CandidateStager(self.people, self.staging, clock, self.sources, self.audit)
        self.stage_candidates = StageCandidates(self.stager)
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

    def sessions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM import_source_sessions ORDER BY created_at, id").fetchall()

    def mappings(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM import_candidate_mappings ORDER BY candidate_id").fetchall()

    def changelog(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM changelog ORDER BY inserted_at, op_id").fetchall()


@pytest.fixture
def harness() -> _Harness:
    return _Harness(open_db(":memory:"))


def _person(ref: str, name: str, email: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": ref,
        "name": name,
        "aliases": [{"value": email, "kind": "handle"}],
    }


def _stage(harness: _Harness, digest: str | None = _DIGEST, **overrides: Any) -> Any:
    candidates: list[dict[str, Any]] = overrides.pop(
        "candidates",
        [
            _person("alice", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "alice", "predicate": "city", "value": "Berlin"},
        ],
    )
    return harness.stage_candidates.execute(
        overrides.pop("source", "weekly-sync"),
        candidates,
        source_kind=overrides.pop("source_kind", "meeting_transcript"),
        content_digest=digest,
        **overrides,
    )


# -- claims ------------------------------------------------------------


def test_staging_publishes_a_receipt_and_its_batch_together(harness: _Harness) -> None:
    batch = _stage(harness)

    sessions = harness.sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == batch.source_session_id
    assert sessions[0]["batch_id"] == batch.batch_id
    assert sessions[0]["status"] == STATUS_STAGED
    assert sessions[0]["content_digest"] == _DIGEST
    assert not batch.duplicate
    assert len(harness.review.execute(batch.batch_id).candidates) == 2


def test_a_repeat_of_the_same_claim_reports_the_existing_batch_and_stages_nothing(harness: _Harness) -> None:
    first = _stage(harness)

    second = _stage(harness)

    assert second.duplicate
    assert second.batch_id == first.batch_id
    assert second.source_session_id == first.source_session_id
    assert second.candidate_count == first.candidate_count
    assert len(harness.sessions()) == 1
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 2


def test_a_different_digest_is_a_different_claim(harness: _Harness) -> None:
    first = _stage(harness)

    second = _stage(harness, digest=_OTHER_DIGEST)

    assert not second.duplicate
    assert second.batch_id != first.batch_id
    assert len(harness.sessions()) == 2


def test_a_different_source_kind_is_a_different_claim(harness: _Harness) -> None:
    _stage(harness)

    second = _stage(harness, source_kind="call_note")

    assert not second.duplicate
    assert len(harness.sessions()) == 2


def test_a_supplied_fingerprint_does_not_alias_to_the_absent_state(harness: _Harness) -> None:
    absent = _stage(harness)

    supplied = _stage(harness, extraction_fingerprint=_FINGERPRINT)

    assert not supplied.duplicate
    assert supplied.batch_id != absent.batch_id
    claim_keys = [row["claim_key"] for row in harness.sessions()]
    assert len(set(claim_keys)) == 2


def test_two_digest_and_no_fingerprint_sessions_cannot_both_claim_one_source(harness: _Harness) -> None:
    """SQLite treats NULLs in a UNIQUE index as distinct, so absence must be a real value."""
    _stage(harness)

    repeat = _stage(harness)

    assert repeat.duplicate
    assert harness.conn.execute("SELECT COUNT(*) FROM import_source_sessions").fetchone()[0] == 1


def test_a_digestless_agent_session_asserts_no_duplicate_claim(harness: _Harness) -> None:
    first = _stage(harness, digest=None)
    second = _stage(harness, digest=None)

    assert not first.duplicate
    assert not second.duplicate
    assert first.batch_id != second.batch_id
    assert [row["claim_key"] for row in harness.sessions()] == [None, None]


def test_a_batch_without_source_metadata_stays_untracked(harness: _Harness) -> None:
    batch = harness.stage_candidates.execute(
        "weekly-sync",
        [_person("alice", "Alice Ahmed", "alice@example.com")],
    )

    assert batch.source_session_id is None
    assert harness.sessions() == []


def test_the_claim_uniqueness_index_refuses_a_second_default_claim(harness: _Harness) -> None:
    """The reservation is the ordinary guard; the index is what makes it a guarantee."""
    _stage(harness)
    claim_key = harness.sessions()[0]["claim_key"]

    with pytest.raises(sqlite3.IntegrityError):
        harness.conn.execute(
            """INSERT INTO import_source_sessions
               (id, source_kind, claim_key, content_digest, status, created_at)
               VALUES ('other', 'meeting_transcript', ?, ?, 'staged', '2026-07-20T12:00:00+00:00')""",
            (claim_key, _DIGEST),
        )


def test_a_receipt_creation_is_journalled_through_the_ordinary_mutation_seam(harness: _Harness) -> None:
    batch = _stage(harness, label="Weekly sync")

    audits = harness.conn.execute(
        "SELECT payload_json FROM audit_log WHERE entity_type = 'import_source_session' AND entity_id = ?",
        (batch.source_session_id,),
    ).fetchall()
    assert len(audits) == 1
    assert json.loads(audits[0]["payload_json"])["label"] == "Weekly sync"
    assert any(row["entity_type"] == "import_source_session" for row in harness.changelog())


def test_a_source_tracked_stager_without_an_audit_log_is_refused(harness: _Harness) -> None:
    """Provenance is primary state, so wiring it without a journal is a mistake, not a mode."""
    with pytest.raises(RuntimeError) as excinfo:
        CandidateStager(harness.people, harness.staging, _Clock(), harness.sources)

    assert "audit log" in str(excinfo.value)


def test_a_refused_source_kind_stages_nothing(harness: _Harness) -> None:
    with pytest.raises(ImportPipelineError):
        _stage(harness, source_kind="interview with alice")

    assert harness.sessions() == []
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_receipt_metadata_without_a_source_kind_is_refused(harness: _Harness) -> None:
    """A digest supplied without a kind was a request for duplicate protection, not decoration."""
    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_candidates.execute(
            "weekly-sync",
            [_person("alice", "Alice Ahmed", "alice@example.com")],
            content_digest=_DIGEST,
        )

    assert excinfo.value.code == "invalid_source_metadata"
    assert excinfo.value.details["field"] == "source_kind"
    assert harness.sessions() == []
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field",
    ["content_digest", "extraction_fingerprint", "label", "external_source_id"],
)
def test_every_receipt_field_requires_a_source_kind(harness: _Harness, field: str) -> None:
    value = _DIGEST if field.endswith(("digest", "fingerprint")) else "Weekly sync"

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_candidates.execute(
            "weekly-sync",
            [_person("alice", "Alice Ahmed", "alice@example.com")],
            **{field: value},
        )

    assert excinfo.value.details["field"] == "source_kind"


def test_staging_without_any_receipt_metadata_stays_the_released_contract(harness: _Harness) -> None:
    batch = harness.stage_candidates.execute(
        "weekly-sync",
        [_person("alice", "Alice Ahmed", "alice@example.com")],
    )

    assert batch.source_session_id is None
    assert batch.reviewable is True


# -- commit mappings ---------------------------------------------------


def test_committing_records_one_mapping_per_committed_candidate(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates

    result = harness.commit.execute(batch.batch_id, [row.id for row in rows])

    mappings = {row["candidate_id"]: row for row in harness.mappings()}
    assert sorted(mappings) == sorted(result.committed_ids)
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    fact_row = next(row for row in rows if row.candidate["type"] == "fact")
    assert mappings[person_row.id]["entity_type"] == "person"
    assert mappings[fact_row.id]["entity_type"] == "fact"
    assert all(row["disposition"] == "entity" for row in mappings.values())
    assert all(row["entity_id"] for row in mappings.values())
    assert all(row["source_session_id"] == batch.source_session_id for row in mappings.values())
    stored_person = harness.people.get(mappings[person_row.id]["entity_id"])
    assert stored_person is not None and stored_person.canonical_name == "Alice Ahmed"


def test_one_commit_shares_one_transaction_id_across_every_durable_effect(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    before = {row["op_id"] for row in harness.changelog()}

    harness.commit.execute(batch.batch_id, [row.id for row in rows])

    produced = [row for row in harness.changelog() if row["op_id"] not in before]
    transaction_ids = {row["transaction_id"] for row in produced}
    assert len(transaction_ids) == 1
    assert all(row["transaction_id"] for row in produced)
    # Child entity writes, candidate mappings, and the receipt status change are all in it.
    assert {row["entity_type"] for row in produced} == {
        "person",
        "fact",
        "import_candidate_mapping",
        "import_source_session",
    }


def test_a_commit_phase_failure_rolls_back_every_effect(harness: _Harness) -> None:
    candidates = [
        _person("alice", "Alice Ahmed", "alice@example.com"),
        {
            "type": "interaction",
            "summary": "Weekly sync",
            "participant_refs": ["alice"],
            "date": "2026-07-20T09:00:00+00:00",
        },
    ]
    batch = _stage(harness, candidates=candidates)
    rows = harness.review.execute(batch.batch_id).candidates
    before = {row["op_id"] for row in harness.changelog()}

    class _Failing:
        """Fail the last commit phase, after people and mappings have already been written."""

        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("interaction phase failed")

    harness.commit._record_interaction = _Failing()  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        harness.commit.execute(batch.batch_id, [row.id for row in rows])

    assert [row["candidate_id"] for row in harness.mappings()] == []
    assert {row["op_id"] for row in harness.changelog()} == before
    assert harness.people.find_by_normalized_name("alice ahmed") == []
    assert {row["status"] for row in harness.conn.execute("SELECT status FROM import_staging")} == {"pending"}
    assert {row["status"] for row in harness.sessions()} == {STATUS_STAGED}


def test_committing_everything_marks_the_receipt_committed(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates

    harness.commit.execute(batch.batch_id, [row.id for row in rows])

    assert harness.sessions()[0]["status"] == STATUS_COMMITTED


def test_committing_part_of_a_batch_marks_the_receipt_partially_committed(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    person_row = next(row for row in rows if row.candidate["type"] == "person")

    harness.commit.execute(batch.batch_id, [person_row.id])

    assert harness.sessions()[0]["status"] == STATUS_PARTIALLY_COMMITTED


def test_a_commit_that_resolves_nothing_leaves_the_receipt_staged(harness: _Harness) -> None:
    batch = _stage(harness)

    harness.commit.execute(batch.batch_id, [])

    assert harness.sessions()[0]["status"] == STATUS_STAGED


def test_a_later_commit_resolves_its_dependency_through_the_stored_mapping(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    fact_row = next(row for row in rows if row.candidate["type"] == "fact")
    harness.commit.execute(batch.batch_id, [person_row.id])
    person_id = harness.mappings()[0]["entity_id"]
    # Rename the person so any name-based re-derivation would resolve to nothing.
    harness.conn.execute(
        "UPDATE persons SET canonical_name = 'Renamed', canonical_name_normalized = 'renamed' WHERE id = ?",
        (person_id,),
    )
    harness.conn.execute("DELETE FROM aliases WHERE person_id = ?", (person_id,))
    harness.conn.commit()

    result = harness.commit.execute(batch.batch_id, [fact_row.id])

    assert result.committed_ids == [fact_row.id]
    fact_mapping = next(row for row in harness.mappings() if row["candidate_id"] == fact_row.id)
    stored = harness.records.get_record("fact", fact_mapping["entity_id"])
    assert stored is not None and stored.person_id == person_id


def test_an_already_committed_candidate_is_skipped_rather_than_remapped(harness: _Harness) -> None:
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    accepted = [row.id for row in rows]
    first = harness.commit.execute(batch.batch_id, accepted)
    before = {row["candidate_id"]: row["entity_id"] for row in harness.mappings()}

    second = harness.commit.execute(batch.batch_id, accepted)

    assert second.committed_ids == []
    assert sorted(second.skipped_ids) == sorted(first.committed_ids)
    assert {row["candidate_id"]: row["entity_id"] for row in harness.mappings()} == before


def test_an_untracked_batch_records_no_mapping(harness: _Harness) -> None:
    batch = harness.stage_candidates.execute(
        "weekly-sync",
        [_person("alice", "Alice Ahmed", "alice@example.com")],
    )
    rows = harness.review.execute(batch.batch_id).candidates

    harness.commit.execute(batch.batch_id, [row.id for row in rows])

    assert harness.mappings() == []


def test_a_dependant_is_not_resolved_through_a_retired_identity(harness: _Harness) -> None:
    """A soft-deleted person cannot receive records, so resolving through them fails the commit.

    Declining to resolve leaves the dependant unresolved and committable later — what every other
    unresolvable identity here does — rather than letting the child write's own active-person check
    refuse mid-transaction and take the whole commit down with it.
    """
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    fact_row = next(row for row in rows if row.candidate["type"] == "fact")
    harness.commit.execute(batch.batch_id, [person_row.id])
    person_id = harness.mappings()[0]["entity_id"]
    harness.conn.execute("UPDATE persons SET deleted_at = ? WHERE id = ?", (_NOW.isoformat(), person_id))
    harness.conn.commit()

    result = harness.commit.execute(batch.batch_id, [fact_row.id])

    assert result.committed_ids == []
    assert result.unresolved_ids == [fact_row.id]
    assert [row["candidate_id"] for row in harness.mappings()] == [person_row.id]


def test_a_retired_staged_match_is_not_resolved_through_either(harness: _Harness) -> None:
    """The same rule on the other resolution route: a retained `matched_person_id`."""
    batch = _stage(harness)
    rows = harness.review.execute(batch.batch_id).candidates
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    fact_row = next(row for row in rows if row.candidate["type"] == "fact")
    harness.commit.execute(batch.batch_id, [person_row.id])
    person_id = harness.mappings()[0]["entity_id"]
    # Pin the retained staged match at the identity that is about to be retired, so the first
    # resolution route is exercised rather than the mapping one.
    candidate = dict(person_row.candidate)
    candidate["matched_person_id"] = person_id
    harness.conn.execute(
        "UPDATE import_staging SET candidate_json = ? WHERE id = ?",
        (json.dumps(candidate), person_row.id),
    )
    harness.conn.execute("UPDATE persons SET deleted_at = ? WHERE id = ?", (_NOW.isoformat(), person_id))
    harness.conn.commit()

    result = harness.commit.execute(batch.batch_id, [fact_row.id])

    assert result.committed_ids == []
    assert result.unresolved_ids == [fact_row.id]
