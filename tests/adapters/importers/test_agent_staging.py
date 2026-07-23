"""Strict agent candidate staging, reference rewriting, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
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
    SetAffiliation,
)
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _use_cases(conn):
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging_store = SqliteImportStagingStore(conn)
    stager = StageCandidates(CandidateStager(people, staging_store, _Clock()))
    remember = RememberPerson(people, people, audit, _Clock())
    interactions = RecordInteraction(people, records, audit, _Clock())
    affiliations = SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, _Clock())
    facts = RecordFact(people, records, audit, _Clock())
    return (
        people,
        stager,
        ReviewImport(staging_store),
        CommitImport(people, staging_store, remember, interactions, affiliations, facts),
    )


def _person(ref: str, name: str, email: str) -> dict:
    return {
        "type": "person",
        "ref": ref,
        "name": name,
        "aliases": [{"value": email, "kind": "handle"}],
    }


def test_agent_person_and_interaction_stage_commit_with_prefixed_provenance() -> None:
    conn = open_db(":memory:")
    _, stage, review, commit = _use_cases(conn)
    result = stage.execute(
        "meeting-notes",
        [
            _person("alice", "Alice", "alice@example.com"),
            _person("bob", "Bob", "bob@example.com"),
            {
                "type": "interaction",
                "summary": "Reviewed the SQL migration",
                "participant_refs": ["alice", "bob"],
                "date": "2026-07-16T10:00:00+00:00",
                "channel": "meeting",
                "sensitivity": "personal",
            },
        ],
    )
    rows = review.execute(result.batch_id).candidates

    assert result.skipped_message_ids == []
    assert result.skipped_without_id == 0
    assert result.skipped_cards == []
    assert {row.source for row in rows} == {"import/agent:meeting-notes"}
    interaction = next(row for row in rows if row.candidate["type"] == "interaction")
    people = [row for row in rows if row.candidate["type"] == "person"]
    assert "participant_refs" not in interaction.candidate
    assert set(interaction.candidate["participant_candidate_ids"]) == {row.id for row in people}

    committed = commit.execute(result.batch_id, [row.id for row in rows])

    assert len(committed.committed_ids) == 3
    stored = conn.execute("SELECT provenance_source, summary FROM interactions").fetchone()
    assert tuple(stored) == ("import/agent:meeting-notes", "Reviewed the SQL migration")


def test_agent_interaction_remains_pending_until_new_person_is_committed() -> None:
    conn = open_db(":memory:")
    _, stage, review, commit = _use_cases(conn)
    result = stage.execute(
        "notes",
        [
            _person("alice", "Alice", "alice@example.com"),
            {
                "type": "interaction",
                "summary": "Discussed launch",
                "participant_refs": ["alice"],
                "date": "2026-07-16T10:00:00+00:00",
            },
        ],
    )
    rows = review.execute(result.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    interaction = next(row for row in rows if row.candidate["type"] == "interaction")

    assert commit.execute(result.batch_id, [interaction.id]).unresolved_ids == [interaction.id]
    assert commit.execute(result.batch_id, [person.id]).committed_ids == [person.id]
    assert commit.execute(result.batch_id, [interaction.id]).committed_ids == [interaction.id]


def test_agent_existing_email_match_resolves_without_accepting_person_candidate() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit = _use_cases(conn)
    existing = Person(
        canonical_name="Alice Existing",
        aliases=[Alias(value="alice@example.com", kind=AliasKind.HANDLE)],
    )
    people.save_person(existing)
    result = stage.execute(
        "notes",
        [
            _person("alice", "Different Display Name", "alice@example.com"),
            {
                "type": "interaction",
                "summary": "Discussed launch",
                "participant_refs": ["alice"],
                "date": "2026-07-16T10:00:00+00:00",
            },
        ],
    )
    rows = review.execute(result.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    interaction = next(row for row in rows if row.candidate["type"] == "interaction")

    assert person.candidate["matched_person_id"] == existing.id
    assert commit.execute(result.batch_id, [interaction.id]).committed_ids == [interaction.id]
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_agent_unique_non_handle_alias_binds_duplicate_name_dependent_without_accepting_person() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit = _use_cases(conn)
    intended = Person(
        canonical_name="Sam Lee",
        aliases=[Alias(value="Sammy", kind=AliasKind.NICKNAME)],
    )
    same_named_contact = Person(
        canonical_name="Sam Lee",
        aliases=[Alias(value="S. Lee", kind=AliasKind.FORMER_NAME)],
    )
    people.save_person(intended)
    people.save_person(same_named_contact)
    result = stage.execute(
        "notes",
        [
            {
                "type": "person",
                "ref": "sam",
                "name": "Sammy",
                "aliases": [],
            },
            {
                "type": "fact",
                "person_ref": "sam",
                "predicate": "location",
                "value": "Dubai",
            },
        ],
    )
    rows = review.execute(result.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    fact = next(row for row in rows if row.candidate["type"] == "fact")

    assert person.candidate["matched_person_id"] == intended.id
    assert commit.execute(result.batch_id, [fact.id]).committed_ids == [fact.id]
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    stored_fact = conn.execute("SELECT person_id, predicate, value FROM facts").fetchone()
    assert tuple(stored_fact) == (intended.id, "location", "Dubai")
    reviewed_person = next(row for row in review.execute(result.batch_id).candidates if row.id == person.id)
    assert reviewed_person.status == "pending"


@pytest.mark.parametrize(
    "candidates",
    [
        [{**_person("alice", "Alice", "alice@example.com"), "unexpected": "forbidden"}],
        [{"type": "observation", "ref": "x", "text": "bad type"}],
        [
            _person("alice", "Alice", "alice@example.com"),
            _person("alice", "Alice Two", "alice2@example.com"),
        ],
        [
            {
                "type": "interaction",
                "summary": "Unknown participant",
                "participant_refs": ["missing"],
                "date": "2026-07-16T10:00:00+00:00",
            }
        ],
    ],
)
def test_invalid_agent_batches_are_strict_and_atomic(candidates: list[dict]) -> None:
    conn = open_db(":memory:")
    _, stage, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as exc_info:
        stage.execute("notes", candidates)

    error = exc_info.value
    assert error.code == "invalid_candidates"
    assert error.details["allowed_types"] == ["person", "interaction", "affiliation", "fact"]
    assert set(error.details["valid_fields"]) == {"person", "interaction", "affiliation", "fact"}
    assert error.details["details"]
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_agent_source_must_be_nonblank() -> None:
    conn = open_db(":memory:")
    _, stage, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as exc_info:
        stage.execute("  ", [_person("alice", "Alice", "alice@example.com")])

    assert exc_info.value.code == "invalid_candidates"
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
