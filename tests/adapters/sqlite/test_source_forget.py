"""Hard forget of import provenance, retained staging, and receipt metadata."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteForgetStore,
    SqliteImportSourceStore,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.imports import CandidateStager, CommitImport, ReviewImport, StageCandidates
from people_context.app.people import Forget, PreviewForget, RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.ports.sources import STATUS_REDACTED

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64

#: Caller-authored wording that must not survive a forget that touches its source.
_LABEL_SENTINEL = "Interview with Alice"
_EXTERNAL_SENTINEL = "NOTES-ALICE-2026-07-20"


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
        self.forget_store = SqliteForgetStore(conn)
        self.forget = Forget(self.people, self.forget_store, clock, self.audit)
        self.preview = PreviewForget(self.people, self.forget_store)

    def sessions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM import_source_sessions ORDER BY id").fetchall()

    def mappings(self) -> dict[str, sqlite3.Row]:
        return {
            row["candidate_id"]: row
            for row in self.conn.execute("SELECT * FROM import_candidate_mappings").fetchall()
        }

    def staging_ids(self) -> set[str]:
        return {row["id"] for row in self.conn.execute("SELECT id FROM import_staging")}

    def audit_payloads(self) -> str:
        return " ".join(row["payload_json"] for row in self.conn.execute("SELECT payload_json FROM audit_log"))

    def changelog_payloads(self) -> str:
        return " ".join(
            f"{row['payload_json']} {row['actor_json']}"
            for row in self.conn.execute("SELECT payload_json, actor_json FROM changelog")
        )


@pytest.fixture
def harness() -> _Harness:
    return _Harness(open_db(":memory:"))


def _person(ref: str, name: str, email: str) -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": [{"value": email, "kind": "handle"}]}


def _stage(harness: _Harness, candidates: list[dict[str, Any]], **overrides: Any) -> Any:
    return harness.stage.execute(
        overrides.pop("source", "weekly-sync"),
        candidates,
        source_kind=overrides.pop("source_kind", "meeting_transcript"),
        content_digest=overrides.pop("content_digest", _DIGEST),
        label=overrides.pop("label", _LABEL_SENTINEL),
        external_source_id=overrides.pop("external_source_id", _EXTERNAL_SENTINEL),
        **overrides,
    )


def _commit_all(harness: _Harness, batch_id: str) -> dict[str, str]:
    rows = harness.review.execute(batch_id).candidates
    harness.commit.execute(batch_id, [row.id for row in rows])
    return {row.candidate.get("name", row.candidate["type"]): row.id for row in rows}


def test_forgetting_a_person_removes_the_mappings_to_what_it_erased(harness: _Harness) -> None:
    batch = _stage(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a", "predicate": "city", "value": "Berlin"},
        ],
    )
    ids = _commit_all(harness, batch.batch_id)
    person_id = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    result = harness.forget.execute(person_id, "person")

    assert harness.mappings() == {}
    assert result.deleted["candidate_mappings"] == 2


def test_a_person_forget_preview_counts_the_provenance_it_would_remove(harness: _Harness) -> None:
    batch = _stage(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a", "predicate": "city", "value": "Berlin"},
        ],
    )
    ids = _commit_all(harness, batch.batch_id)
    person_id = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    preview = harness.preview.execute(person_id)
    result = harness.forget.execute(person_id, "person")

    assert preview.deleted["candidate_mappings"] == result.deleted["candidate_mappings"]
    assert preview.deleted["import_staging"] == result.deleted["import_staging"]


def test_a_pending_candidate_matching_the_forgotten_person_is_removed(harness: _Harness) -> None:
    committed = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, committed.batch_id)
    person_id = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]
    pending = _stage(
        harness,
        [
            _person("a2", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a2", "predicate": "city", "value": "Berlin"},
        ],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    pending_rows = harness.review.execute(pending.batch_id).candidates
    assert len(pending_rows) == 2
    assert {row.id for row in pending_rows} <= harness.staging_ids()

    harness.forget.execute(person_id, "person")

    # The matched person row and the fact that depended on it are both gone: a dependent row
    # left behind would reference a candidate that no longer exists.
    assert harness.staging_ids().isdisjoint({row.id for row in pending_rows})
    assert harness.staging_ids() == set()


def test_dependency_deletion_reaches_a_fixed_point(harness: _Harness) -> None:
    committed = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, committed.batch_id)
    person_id = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]
    pending = _stage(
        harness,
        [
            _person("a2", "Alice Ahmed", "alice@example.com"),
            _person("b", "Bob Byrne", "bob@example.com"),
            {
                "type": "interaction",
                "summary": "Weekly sync",
                "participant_refs": ["a2", "b"],
                "date": "2026-07-20T09:00:00+00:00",
            },
            {"type": "relationship", "from_ref": "a2", "to_ref": "b", "relationship_type": "colleague of"},
        ],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    staged = harness.review.execute(pending.batch_id).candidates
    rows = {row.candidate.get("name", row.candidate["type"]): row.id for row in staged}

    harness.forget.execute(person_id, "person")

    remaining = harness.staging_ids()
    # Bob's own candidate has no reference to the removed one and survives; everything that
    # named the erased candidate — directly or through another removed row — is gone.
    assert rows["Bob Byrne"] in remaining
    assert rows["Alice Ahmed"] not in remaining
    assert rows["interaction"] not in remaining
    assert rows["relationship"] not in remaining


def test_staging_deletion_never_guesses_by_name(harness: _Harness) -> None:
    """A different person who happens to share a name keeps their staged candidate."""
    committed = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, committed.batch_id)
    person_id = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]
    unrelated = harness.stage.execute(
        "other-note",
        [{"type": "person", "ref": "x", "name": "Alice Ahmed", "aliases": []}],
        source_kind="call_note",
        content_digest="d" * 64,
    )
    unrelated_row = harness.review.execute(unrelated.batch_id).candidates[0]
    # Break the canonical match so only the name still connects the two.
    harness.conn.execute(
        "UPDATE import_staging SET candidate_json = ? WHERE id = ?",
        (json.dumps({"type": "person", "name": "Alice Ahmed", "aliases": []}), unrelated_row.id),
    )
    harness.conn.commit()

    harness.forget.execute(person_id, "person")

    assert unrelated_row.id in harness.staging_ids()


def test_a_shared_interaction_keeps_its_mapping_when_one_participant_is_forgotten(harness: _Harness) -> None:
    batch = _stage(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Bob Byrne", "bob@example.com"),
            {
                "type": "interaction",
                "summary": "Weekly sync",
                "participant_refs": ["a", "b"],
                "date": "2026-07-20T09:00:00+00:00",
            },
        ],
    )
    ids = _commit_all(harness, batch.batch_id)
    mappings = harness.mappings()
    alice = mappings[ids["Alice Ahmed"]]["entity_id"]
    interaction_id = mappings[ids["interaction"]]["entity_id"]

    harness.forget.execute(alice, "person")

    after = harness.mappings()
    assert after[ids["interaction"]]["entity_id"] == interaction_id
    assert harness.records.get_record("interaction", interaction_id) is not None
    assert ids["Alice Ahmed"] not in after


def test_a_surviving_multi_person_source_keeps_its_mappings_but_loses_its_caller_metadata(
    harness: _Harness,
) -> None:
    batch = _stage(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Bob Byrne", "bob@example.com"),
        ],
    )
    ids = _commit_all(harness, batch.batch_id)
    mappings = harness.mappings()
    alice = mappings[ids["Alice Ahmed"]]["entity_id"]
    # The wording is genuinely in history before the forget, so its absence afterwards is a
    # redaction rather than a test that never had anything to find.
    assert _LABEL_SENTINEL in harness.audit_payloads()
    assert _LABEL_SENTINEL in harness.changelog_payloads()

    harness.forget.execute(alice, "person")

    session = harness.sessions()[0]
    assert session["status"] != STATUS_REDACTED
    assert session["label"] is None
    assert session["external_source_id"] is None
    # Bob's provenance is untouched and still usable.
    assert harness.mappings()[ids["Bob Byrne"]]["entity_id"] == mappings[ids["Bob Byrne"]]["entity_id"]
    assert session["batch_id"] == batch.batch_id
    assert _LABEL_SENTINEL not in harness.audit_payloads()
    assert _EXTERNAL_SENTINEL not in harness.audit_payloads()
    assert _LABEL_SENTINEL not in harness.changelog_payloads()


def test_a_fully_forgotten_claim_backed_source_becomes_a_minimal_terminal_receipt(harness: _Harness) -> None:
    batch = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, batch.batch_id)
    alice = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    harness.forget.execute(alice, "person")

    session = harness.sessions()[0]
    assert session["status"] == STATUS_REDACTED
    assert session["content_digest"] == _DIGEST
    assert session["source_kind"] == "meeting_transcript"
    assert session["claim_key"] is not None
    assert session["batch_id"] is None
    assert session["label"] is None
    assert session["external_source_id"] is None
    assert session["extraction_contract_revision"] is None
    assert harness.staging_ids() == set()
    assert harness.mappings() == {}


def test_a_fully_forgotten_digestless_source_is_deleted_entirely(harness: _Harness) -> None:
    batch = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")], content_digest=None)
    ids = _commit_all(harness, batch.batch_id)
    alice = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    harness.forget.execute(alice, "person")

    assert harness.sessions() == []
    assert harness.staging_ids() == set()
    # Nothing suppresses staging similar material again: there never was a claim to keep.
    again = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")], content_digest=None)
    assert not again.duplicate


def test_a_record_forget_removes_only_that_records_mapping(harness: _Harness) -> None:
    batch = _stage(
        harness,
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a", "predicate": "city", "value": "Berlin"},
        ],
    )
    ids = _commit_all(harness, batch.batch_id)
    mappings = harness.mappings()
    fact_id = mappings[ids["fact"]]["entity_id"]

    result = harness.forget.execute(f"fact:{fact_id}", "record")

    after = harness.mappings()
    assert ids["fact"] not in after
    assert after[ids["Alice Ahmed"]]["entity_id"] == mappings[ids["Alice Ahmed"]]["entity_id"]
    assert result.deleted["candidate_mappings"] == 1
    assert harness.sessions()[0]["status"] != STATUS_REDACTED
    assert harness.sessions()[0]["label"] is None


def test_removed_mappings_appear_in_the_forget_replay_manifest(harness: _Harness) -> None:
    batch = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, batch.batch_id)
    alice = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    harness.forget.execute(alice, "person")

    tombstones = [
        json.loads(row["payload_json"])
        for row in harness.conn.execute("SELECT payload_json FROM changelog WHERE op_kind = 'forget'")
    ]
    affected = {
        (entity["entity_type"], entity["entity_id"])
        for tombstone in tombstones
        for entity in tombstone.get("affected_entities", [])
    }
    assert ("import_candidate_mapping", ids["Alice Ahmed"]) in affected


def test_mapping_history_is_redacted_rather_than_left_replayable(harness: _Harness) -> None:
    batch = _stage(harness, [_person("a", "Alice Ahmed", "alice@example.com")])
    ids = _commit_all(harness, batch.batch_id)
    alice = harness.mappings()[ids["Alice Ahmed"]]["entity_id"]

    harness.forget.execute(alice, "person")

    surviving = [
        row
        for row in harness.conn.execute(
            "SELECT payload_json FROM changelog WHERE entity_type = 'import_candidate_mapping'"
        )
    ]
    assert surviving, "the mapping's replay history is expected to exist and be redacted, not deleted"
    assert all(json.loads(row["payload_json"]) == {"redacted": True} for row in surviving)
    assert alice not in harness.audit_payloads()
