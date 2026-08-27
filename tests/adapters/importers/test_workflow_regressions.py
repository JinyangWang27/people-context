"""Import workflow regression tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    open_db,
)
from people_context.app.imports import CandidateStager, CommitImport
from people_context.app.people import AmbiguousPersonError, RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.domain.person import Person

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _person(name: str) -> Person:
    return Person(
        canonical_name=name,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _commit_import(
    conn: sqlite3.Connection,
) -> tuple[SqlitePeopleRepository, SqliteImportStagingStore, CandidateStager, CommitImport]:
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    clock = _Clock()
    commit = CommitImport(
        people,
        staging,
        RememberPerson(people, people, audit, clock),
        RecordInteraction(people, records, audit, clock),
        SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, clock),
        RecordFact(people, records, audit, clock),
        RecordObservation(people, records, audit, clock),
        RecordTrait(people, records, audit, clock),
        SetRelationship(people, SqliteRelationshipStore(conn), audit, clock),
    )
    return people, staging, CandidateStager(people, staging, clock), commit


def test_commit_import_is_atomic_when_a_later_candidate_fails() -> None:
    conn = open_db(":memory:")
    people, staging, stager, commit = _commit_import(conn)
    people.save_person(_person("Sam"))
    people.save_person(_person("sam"))
    batch = stager.execute(
        "import/agent:test",
        [
            {"type": "person", "ref": "u", "name": "Unique Person", "aliases": []},
            {"type": "person", "ref": "s", "name": "Sam", "aliases": []},
        ],
    )
    rows = staging.list_batch(batch.batch_id)

    with pytest.raises(AmbiguousPersonError):
        commit.execute(batch.batch_id, [row.id for row in rows])

    names = {person.canonical_name for person in people.list_people()}
    assert "Unique Person" not in names
    assert all(row.status == "pending" for row in staging.list_batch(batch.batch_id))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
