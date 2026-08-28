"""Bundle version-2 import state: export, restore, baseline-empty, and version acceptance."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteBootstrapRestorer,
    SqliteBundleReader,
    SqliteForgetStore,
    SqliteHybridLogicalClock,
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
from people_context.app.exports import ExportSyncBundle
from people_context.app.exports.sync_bundle import render_bundle_json
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
from people_context.app.sync import RestoreSyncBundle
from people_context.domain.sync_bundle import (
    SYNC_BUNDLE_VERSION,
    InvalidBundleError,
    TargetNotEmptyError,
)

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Origin:
    """A source database that stages, commits, and can be exported."""

    def __init__(self, path: Path) -> None:
        self.conn = open_db(path)
        clock = _Clock()
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.staging = SqliteImportStagingStore(self.conn)
        self.sources = SqliteImportSourceStore(self.conn)
        self.stage = StageCandidates(
            CandidateStager(self.people, self.staging, clock, self.sources, self.audit)
        )
        self.review = ReviewImport(self.staging)
        self.commit = CommitImport(
            self.people,
            self.staging,
            RememberPerson(self.people, self.people, self.audit, clock),
            RecordInteraction(self.people, self.records, self.audit, clock),
            SetAffiliation(self.people, SqliteOrganizationStore(self.conn), self.records, self.audit, clock),
            RecordFact(self.people, self.records, self.audit, clock),
            RecordObservation(self.people, self.records, self.audit, clock),
            RecordTrait(self.people, self.records, self.audit, clock),
            SetRelationship(
                self.people,
                SqliteRelationshipStore(self.conn),
                self.audit,
                clock,
                SqliteRelationshipVocabularyStore(self.conn),
            ),
            self.sources,
            self.audit,
            clock,
        )
        self.merge = MergePeople(self.people, SqliteMergeStore(self.conn), clock, self.audit)
        self.forget = SqliteForgetStore(self.conn)

    def export(self) -> Any:
        return ExportSyncBundle(SqliteBundleReader(self.conn), _Clock()).execute()


def _person(ref: str, name: str, email: str) -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": [{"value": email, "kind": "handle"}]}


def _destination(path: Path) -> tuple[sqlite3.Connection, SqliteBootstrapRestorer]:
    conn = open_db(path)
    repo = SqlitePeopleRepository(conn)
    return conn, SqliteBootstrapRestorer(conn, repo, SqliteHybridLogicalClock(conn))


def _restore(document: Any, path: Path) -> tuple[sqlite3.Connection, Any]:
    conn, restorer = _destination(path)
    outcome = RestoreSyncBundle(restorer).execute(document)
    return conn, outcome


class _NullRestorer:
    """Stands in for the port when only parsing and validation are under test."""

    def restore(self, document: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("parsing tests do not restore")


def _parse(text: str) -> Any:
    """Parse and document-validate one rendered bundle without touching a destination."""
    return RestoreSyncBundle(_NullRestorer()).parse(text)


def _round_trip(document: Any) -> Any:
    """Re-parse a rendered bundle so restore sees exactly what a file would carry."""
    return _parse(render_bundle_json(document))


def test_export_emits_the_current_version_with_import_state(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a", "predicate": "city", "value": "Berlin"},
        ],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
        label="Weekly sync",
    )
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [rows[0].id])

    document = origin.export()

    assert document.version == SYNC_BUNDLE_VERSION == 2
    assert [session.id for session in document.imports.source_sessions] == [batch.source_session_id]
    assert len(document.imports.candidate_mappings) == 1
    # The batch is only partially committed, so its reviewable rows travel.
    assert {row.id for row in document.imports.staging} == {row.id for row in rows}


def test_a_completed_source_keeps_its_mappings_after_staging_cleanup(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [row.id for row in rows])
    # A completed batch's operational rows are not needed anywhere else; drop them the way a
    # later cleanup policy would and prove the durable provenance still travels.
    origin.conn.execute("DELETE FROM import_staging WHERE batch_id = ?", (batch.batch_id,))
    origin.conn.commit()

    document = _round_trip(origin.export())

    assert document.imports.staging == []
    assert len(document.imports.candidate_mappings) == 1
    conn, outcome = _restore(document, tmp_path / "restored.db")
    assert outcome.candidate_mappings == 1
    assert outcome.staged_candidates == 0
    restored = conn.execute("SELECT * FROM import_candidate_mappings").fetchall()
    assert restored[0]["entity_id"] == document.imports.candidate_mappings[0].entity_id
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_a_partially_committed_source_restores_reviewable_and_committable(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            {"type": "fact", "person_ref": "a", "predicate": "city", "value": "Berlin"},
        ],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = origin.review.execute(batch.batch_id).candidates
    person_row = next(row for row in rows if row.candidate["type"] == "person")
    fact_row = next(row for row in rows if row.candidate["type"] == "fact")
    origin.commit.execute(batch.batch_id, [person_row.id])

    document = _round_trip(origin.export())
    conn, _outcome = _restore(document, tmp_path / "restored.db")

    restored = _Origin(tmp_path / "restored.db")
    review = restored.review.execute(batch.batch_id)
    assert {row.id for row in review.candidates} == {person_row.id, fact_row.id}
    result = restored.commit.execute(batch.batch_id, [fact_row.id])
    assert result.committed_ids == [fact_row.id]
    # The dependency resolved through the restored mapping, not through a name guess.
    mapping = conn.execute(
        "SELECT entity_id FROM import_candidate_mappings WHERE candidate_id = ?", (person_row.id,)
    ).fetchone()
    stored = restored.records.get_record(
        "fact",
        restored.conn.execute(
            "SELECT entity_id FROM import_candidate_mappings WHERE candidate_id = ?", (fact_row.id,)
        ).fetchone()["entity_id"],
    )
    assert stored is not None and stored.person_id == mapping["entity_id"]


def test_a_merge_retargeted_match_is_preserved_verbatim(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    committed = origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com"), _person("b", "Ally Ahmed", "ally@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = origin.review.execute(committed.batch_id).candidates
    origin.commit.execute(committed.batch_id, [row.id for row in rows])
    mappings = {
        row["candidate_id"]: row["entity_id"]
        for row in origin.conn.execute("SELECT candidate_id, entity_id FROM import_candidate_mappings")
    }
    primary = mappings[next(row.id for row in rows if row.candidate["name"] == "Alice Ahmed")]
    duplicate = mappings[next(row.id for row in rows if row.candidate["name"] == "Ally Ahmed")]
    pending = origin.stage.execute(
        "call-note",
        [_person("b2", "Ally Ahmed", "ally@example.com")],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    origin.merge.execute(primary, duplicate)

    document = _round_trip(origin.export())

    staged = [row for row in document.imports.staging if row.batch_id == pending.batch_id]
    assert [row.candidate["matched_person_id"] for row in staged] == [primary]
    conn, _outcome = _restore(document, tmp_path / "restored.db")
    restored_rows = conn.execute("SELECT candidate_json FROM import_staging").fetchall()
    assert all(json.loads(row["candidate_json"])["matched_person_id"] == primary for row in restored_rows)


def test_a_terminal_redacted_receipt_round_trips_without_its_cleared_metadata(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
        label="Interview with Alice",
    )
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [row.id for row in rows])
    person_id = origin.conn.execute("SELECT entity_id FROM import_candidate_mappings").fetchone()["entity_id"]
    origin.forget.forget_person(person_id)

    document = _round_trip(origin.export())

    assert [session.status for session in document.imports.source_sessions] == ["redacted"]
    session = document.imports.source_sessions[0]
    assert session.label is None and session.batch_id is None and session.content_digest == _DIGEST
    assert "Interview with Alice" not in render_bundle_json(document)
    conn, outcome = _restore(document, tmp_path / "restored.db")
    assert outcome.source_sessions == 1
    restored = conn.execute("SELECT * FROM import_source_sessions").fetchone()
    assert restored["status"] == "redacted"
    assert restored["label"] is None


def test_a_redacted_receipt_carrying_cleared_metadata_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
        label="Interview with Alice",
    )
    payload = json.loads(render_bundle_json(origin.export()))
    payload["imports"]["source_sessions"][0]["status"] = "redacted"

    with pytest.raises(InvalidBundleError):
        _parse(json.dumps(payload))


def test_a_mapping_to_an_unbundled_entity_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [row.id for row in rows])
    payload = json.loads(render_bundle_json(origin.export()))
    payload["imports"]["candidate_mappings"][0]["entity_id"] = "01J0000000000000000MISSING"

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("unbundled person" in detail for detail in excinfo.value.details)


def test_a_staging_row_whose_match_is_not_active_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    payload = json.loads(render_bundle_json(origin.export()))
    payload["imports"]["staging"][0]["candidate"]["matched_person_id"] = "01J0000000000000000MISSING"

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("not active in the bundle" in detail for detail in excinfo.value.details)


def test_a_staging_row_without_a_bundled_receipt_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute(
        "weekly-sync",
        [_person("a", "Alice Ahmed", "alice@example.com")],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    payload = json.loads(render_bundle_json(origin.export()))
    payload["imports"]["source_sessions"] = []

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("no bundled source session" in detail for detail in excinfo.value.details)


def test_a_merged_away_mapping_needs_no_live_entity(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute(
        "weekly-sync",
        [
            _person("a", "Alice Ahmed", "alice@example.com"),
            _person("b", "Ally Ahmed", "ally@example.com"),
            {"type": "relationship", "from_ref": "a", "to_ref": "b", "relationship_type": "colleague of"},
        ],
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
    )
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [row.id for row in rows])
    mappings = {
        row["candidate_id"]: row["entity_id"]
        for row in origin.conn.execute("SELECT candidate_id, entity_id FROM import_candidate_mappings")
    }
    primary = mappings[next(row.id for row in rows if row.candidate.get("name") == "Alice Ahmed")]
    duplicate = mappings[next(row.id for row in rows if row.candidate.get("name") == "Ally Ahmed")]
    origin.merge.execute(primary, duplicate)

    document = _round_trip(origin.export())
    conn, outcome = _restore(document, tmp_path / "restored.db")

    terminal = conn.execute(
        "SELECT * FROM import_candidate_mappings WHERE disposition = 'merged_away'"
    ).fetchall()
    assert len(terminal) == 1
    assert terminal[0]["entity_id"] is None
    assert outcome.candidate_mappings == len(document.imports.candidate_mappings)


# -- version acceptance and baseline emptiness -------------------------


def test_a_version_one_bundle_still_restores(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    batch = origin.stage.execute("weekly-sync", [_person("a", "Alice Ahmed", "alice@example.com")])
    rows = origin.review.execute(batch.batch_id).candidates
    origin.commit.execute(batch.batch_id, [row.id for row in rows])
    origin.conn.execute("DELETE FROM import_staging")
    origin.conn.commit()
    payload = json.loads(render_bundle_json(origin.export()))
    payload["version"] = 1
    payload.pop("imports")

    document = _parse(json.dumps(payload))
    conn, outcome = _restore(document, tmp_path / "restored.db")

    assert outcome.source_sessions == 0
    assert outcome.candidate_mappings == 0
    assert conn.execute("SELECT COUNT(*) FROM import_source_sessions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_a_version_one_bundle_carrying_version_two_state_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute("weekly-sync", [_person("a", "Alice Ahmed", "alice@example.com")])
    payload = json.loads(render_bundle_json(origin.export()))
    payload["version"] = 1

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("imports" in detail for detail in excinfo.value.details)


@pytest.mark.parametrize("version", [1, 2])
def test_a_non_empty_import_table_refuses_every_accepted_version(tmp_path: Path, version: int) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute("weekly-sync", [_person("a", "Alice Ahmed", "alice@example.com")])
    payload = json.loads(render_bundle_json(origin.export()))
    if version == 1:
        payload["version"] = 1
        payload.pop("imports")
    document = _parse(json.dumps(payload))

    destination = _Origin(tmp_path / "destination.db")
    destination.stage.execute(
        "local-note",
        [_person("z", "Zed Zephyr", "zed@example.com")],
        source_kind="call_note",
        content_digest=_OTHER_DIGEST,
    )
    destination.conn.execute("DELETE FROM import_staging")
    destination.conn.execute("DELETE FROM persons")
    destination.conn.execute("DELETE FROM aliases")
    destination.conn.execute("DELETE FROM person_search")
    destination.conn.execute("DELETE FROM audit_log")
    destination.conn.execute("DELETE FROM changelog")
    destination.conn.commit()
    _conn, restorer = _destination(tmp_path / "destination.db")

    with pytest.raises(TargetNotEmptyError) as excinfo:
        restorer.restore(document)

    assert any("import_source_sessions" in detail for detail in excinfo.value.details)


def test_a_non_empty_mapping_table_refuses_a_restore(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    origin.stage.execute("weekly-sync", [_person("a", "Alice Ahmed", "alice@example.com")])
    document = _round_trip(origin.export())

    destination_path = tmp_path / "destination.db"
    destination = open_db(destination_path)
    destination.execute(
        """INSERT INTO import_source_sessions (id, source_kind, status, created_at)
           VALUES ('s1', 'linkedin', 'staged', '2026-07-20T12:00:00+00:00')"""
    )
    destination.execute(
        """INSERT INTO import_candidate_mappings
           (candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at)
           VALUES ('c1', 'b1', 's1', 'entity', 'person', 'p1', '2026-07-20T12:00:00+00:00')"""
    )
    destination.commit()
    destination.close()
    _conn, restorer = _destination(destination_path)

    with pytest.raises(TargetNotEmptyError) as excinfo:
        restorer.restore(document)

    assert any("import_candidate_mappings" in detail for detail in excinfo.value.details)
