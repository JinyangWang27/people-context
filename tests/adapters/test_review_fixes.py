"""Regression coverage for the pre-M7 review fixes."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteForgetStore,
    SqliteImportStagingStore,
    SqliteMergeStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePreferencesStore,
    SqliteRecordStore,
    open_db,
)
from people_context.app._mutation import changelog_mutation
from people_context.app.imports.staging import CandidateStager
from people_context.app.imports.workflow import CommitImport
from people_context.app.people.edit import EditPerson, EditPersonInput, PersonNameCollisionError
from people_context.app.people.forget import Forget
from people_context.app.people.merge import MergePeople
from people_context.app.people.remember import AmbiguousPersonError, RememberPerson, RememberPersonInput
from people_context.app.records.affiliations import SetAffiliation
from people_context.app.records.facts import RecordFact
from people_context.app.records.interactions import RecordInteraction
from people_context.cli import main
from people_context.domain.person import Alias, AliasKind, Person
from people_context.ports.audit_log import AuditEntry

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _person(name: str, aliases: list[str] | None = None) -> Person:
    return Person(
        canonical_name=name,
        aliases=[Alias(value=value, kind=AliasKind.OTHER) for value in aliases or []],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _commit_import(conn) -> tuple[SqlitePeopleRepository, SqliteImportStagingStore, CandidateStager, CommitImport]:
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
    )
    return people, staging, CandidateStager(people, staging, clock), commit


def test_commit_import_is_atomic_when_a_later_candidate_fails() -> None:
    conn = open_db(":memory:")
    people, staging, stager, commit = _commit_import(conn)
    # Two active people share the normalized name, so committing "Sam" is ambiguous.
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
    assert "Unique Person" not in names, "earlier candidate must roll back with the failed batch"
    assert all(row.status == "pending" for row in staging.list_batch(batch.batch_id))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0


def test_forget_and_merge_record_the_caller_source() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    merge_store = SqliteMergeStore(conn)
    forget_store = SqliteForgetStore(conn)
    clock = _Clock()
    remember = RememberPerson(people, people, audit, clock)
    primary = remember.execute(RememberPersonInput(name="Keep Person")).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate Person")).person
    doomed = remember.execute(RememberPersonInput(name="Doomed Person")).person

    MergePeople(people, merge_store, clock, audit).execute(primary.id, duplicate.id, source="cli")
    merge_audit = next(entry for entry in audit.list_entries() if entry.op == "merge")
    assert merge_audit.source == "cli"

    Forget(people, forget_store, clock, audit).execute(doomed.id, "person", source="cli")
    forget_audit = next(entry for entry in audit.list_entries() if entry.op == "forget")
    assert forget_audit.source == "cli"
    changelog_forget = next(
        entry for entry in SqliteChangelog(conn).list_entries() if entry.op_kind == "forget"
    )
    assert changelog_forget.actor["source"] == "cli"


def test_edit_person_rejects_rename_onto_another_persons_alias() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    people.save_person(_person("Robert", aliases=["Bobby"]))
    people.save_person(_person("Alice"))
    alice = people.find_by_normalized_name("alice")[0]

    edit = EditPerson(people, people, audit, clock)
    with pytest.raises(PersonNameCollisionError):
        edit.execute(EditPersonInput(person_id=alice.id, name="Bobby"))
    # Renaming onto one of the person's own aliases stays allowed.
    robert = people.find_by_normalized_name("robert")[0]
    edited = edit.execute(EditPersonInput(person_id=robert.id, name="Bobby"))
    assert edited.canonical_name == "Bobby"


def test_partial_sync_capability_wrapper_is_rejected() -> None:
    conn = open_db(":memory:")
    real = SqliteAuditLog(conn)

    class PartialWrapper:
        """Forwards the changelog but forgets the hybrid clock."""

        def __init__(self) -> None:
            self.changelog = real.changelog

        def append(self, entry: AuditEntry) -> None:
            real.append(entry)

    with pytest.raises(RuntimeError, match="hybrid_clock"):
        changelog_mutation(
            PartialWrapper(),
            _Clock(),
            entity_type="person",
            entity_id="p1",
            op_kind="update",
            payload={},
            changed_fields=[],
            transaction_id="t1",
            source="agent",
        )


def test_open_db_sets_busy_timeout(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_cli_export_file_is_owner_only(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "export.json"
    conn = open_db(db_path)
    RememberPerson(
        SqlitePeopleRepository(conn), SqlitePeopleRepository(conn), SqliteAuditLog(conn), _Clock()
    ).execute(RememberPersonInput(name="Alice"))
    conn.close()

    assert main(["--db", str(db_path), "export", "--output", str(output)]) == 0
    mode = stat.S_IMODE(os.stat(output).st_mode)
    assert mode == 0o600
    assert "Alice" in output.read_text(encoding="utf-8")


def test_preferences_store_uses_injected_clock() -> None:
    conn = open_db(":memory:")
    store = SqlitePreferencesStore(conn, _Clock())
    store.set("communication_philosophy", "be kind")
    row = conn.execute("SELECT updated_at FROM user_preferences WHERE key = 'communication_philosophy'").fetchone()
    assert row["updated_at"] == _NOW.isoformat()


def test_audit_payload_preserves_non_ascii_text() -> None:
    conn = open_db(":memory:")
    audit = SqliteAuditLog(conn)
    entry = AuditEntry(
        ts=_NOW, op="create", entity_type="person", entity_id="p1", payload={"name": "王小明"}, source="user"
    )
    audit.append(entry)
    stored = conn.execute("SELECT payload_json FROM audit_log").fetchone()["payload_json"]
    assert "王小明" in stored
    assert json.loads(stored) == {"name": "王小明"}
