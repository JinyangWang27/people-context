"""Regression coverage for the post-M7 follow-up fixes (migration 004, merge dedupe, vault names)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path

from people_context.adapters.filesystem.vault_writer import sanitize_filename
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteMergeStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.people.merge import MergePeople
from people_context.app.people.remember import RememberPerson, RememberPersonInput
from people_context.app.relationships.commands import SetRelationship, SetRelationshipInput
from people_context.domain.organization import Organization
from people_context.domain.person import Person

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _people_fixture(conn):
    people = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    remember = RememberPerson(people, people, audit, clock)
    return people, audit, clock, remember


def test_migration_004_backfills_org_normalized_name(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    migrations = resources.files("people_context.adapters.sqlite.migrations")
    for name in ("001_initial.sql", "002_sync_foundations.sql", "003_relationship_vocabulary.sql"):
        legacy.executescript(migrations.joinpath(name).read_text(encoding="utf-8"))
    legacy.execute("INSERT INTO organizations (id, name, kind) VALUES ('org-1', '  Acme  CORP ', 'company')")
    legacy.execute("PRAGMA user_version = 3")
    legacy.commit()
    legacy.close()

    conn = open_db(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    row = conn.execute("SELECT name_normalized FROM organizations WHERE id = 'org-1'").fetchone()
    assert row["name_normalized"] == "acme corp"
    indexes = {row[1] for row in conn.execute("PRAGMA index_list('changelog')").fetchall()}
    assert "idx_changelog_entity" in indexes


def test_org_store_uses_indexed_normalized_lookup() -> None:
    conn = open_db(":memory:")
    store = SqliteOrganizationStore(conn)
    store.save(Organization(id="org-1", name="Acme Corp", kind="company"))
    found = store.get_by_normalized_name("acme corp")
    assert found is not None and found.id == "org-1"
    assert store.get_by_normalized_name("unknown org") is None
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM organizations WHERE name_normalized = ?", ("acme corp",)
    ).fetchall()
    assert any("idx_organizations_name_norm" in row[3] for row in plan)


def test_merge_dedupes_overlapping_parallel_edges_and_keeps_history() -> None:
    conn = open_db(":memory:")
    people, audit, clock, remember = _people_fixture(conn)
    store = SqliteRelationshipStore(conn)
    vocabulary = SqliteRelationshipVocabularyStore(conn)
    set_relationship = SetRelationship(people, store, audit, clock, vocabulary)
    primary = remember.execute(RememberPersonInput(name="Primary")).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate")).person
    third = remember.execute(RememberPersonInput(name="Third")).person

    # Parallel open-ended colleague edges from both sides: expect one survivor.
    set_relationship.execute(SetRelationshipInput(subject_id=primary.id, object_id=third.id, type="colleague_of"))
    set_relationship.execute(SetRelationshipInput(subject_id=third.id, object_id=duplicate.id, type="colleague_of"))
    # Disjoint historical reports_to must survive next to the current one.
    set_relationship.execute(
        SetRelationshipInput(
            subject_id=primary.id,
            object_id=third.id,
            type="reports_to",
            valid_from=date(2019, 1, 1),
            valid_to=date(2020, 1, 1),
        )
    )
    set_relationship.execute(
        SetRelationshipInput(
            subject_id=duplicate.id,
            object_id=third.id,
            type="reports_to",
            valid_from=date(2024, 1, 1),
        )
    )

    result = MergePeople(people, SqliteMergeStore(conn), clock, audit).execute(primary.id, duplicate.id)

    assert result.duplicate_relationships_removed == 1
    remaining = [
        (row.type, str(row.period.valid_from))
        for row in SqliteRelationshipStore(conn).list_relationships()
    ]
    assert sorted(remaining) == [
        ("colleague_of", "None"),
        ("reports_to", "2019-01-01"),
        ("reports_to", "2024-01-01"),
    ]
    delete_ops = [
        entry
        for entry in SqliteChangelog(conn).list_entries()
        if entry.op_kind == "delete" and entry.payload.get("merged_into")
    ]
    assert len(delete_ops) == 1, "deduped edge must be captured in the changelog"


def test_sanitize_filename_guards_windows_reserved_and_wikilink_characters() -> None:
    assert sanitize_filename("CON") == "CON_"
    assert sanitize_filename("com1") == "com1_"
    assert sanitize_filename("Nula") == "Nula"
    assert sanitize_filename("NUL.txt") == "NUL_.txt"
    assert sanitize_filename("COM1.notes") == "COM1_.notes"
    assert sanitize_filename("COM¹") == "COM¹_"
    assert sanitize_filename("Dr. Smith") == "Dr. Smith"
    assert sanitize_filename("x]]y|z") == "x__y_z"
    assert sanitize_filename("tag#and^caret") == "tag_and_caret"
    assert sanitize_filename("王小明") == "王小明"


def test_merge_never_dedupes_preexisting_primary_edges() -> None:
    conn = open_db(":memory:")
    people, audit, clock, remember = _people_fixture(conn)
    primary = remember.execute(RememberPersonInput(name="Primary")).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate")).person
    third = remember.execute(RememberPersonInput(name="Third")).person
    # Two overlapping legacy parallel edges on the primary, inserted directly;
    # the duplicate has no relationships at all.
    for index in (1, 2):
        conn.execute(
            """INSERT INTO relationships
               (id, subject_id, object_id, type, label, confidence, provenance_source, created_at)
               VALUES (?, ?, ?, 'mentor_of', ?, 1.0, 'user', ?)""",
            (f"edge-{index}", primary.id, third.id, f"label-{index}", _NOW.isoformat()),
        )
    conn.commit()

    result = MergePeople(people, SqliteMergeStore(conn), clock, audit).execute(primary.id, duplicate.id)

    assert result.duplicate_relationships_removed == 0
    remaining = {row.id for row in SqliteRelationshipStore(conn).list_relationships()}
    assert remaining == {"edge-1", "edge-2"}, "merge must not act as implicit normalize-relationships"


def test_merge_recanonicalizes_symmetric_edges_so_reassert_updates_in_place() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    store = SqliteRelationshipStore(conn)
    vocabulary = SqliteRelationshipVocabularyStore(conn)
    # Explicit IDs force duplicate < third < primary.
    for person_id, name in (("id-a-dup", "Dup"), ("id-b-third", "Third"), ("id-c-primary", "Primary")):
        people.save_person(Person(id=person_id, canonical_name=name, created_at=_NOW, updated_at=_NOW))
    set_relationship = SetRelationship(people, store, audit, clock, vocabulary)
    set_relationship.execute(SetRelationshipInput(subject_id="id-a-dup", object_id="id-b-third", type="friend_of"))

    MergePeople(people, SqliteMergeStore(conn), clock, audit).execute("id-c-primary", "id-a-dup")
    (edge,) = SqliteRelationshipStore(conn).list_relationships()
    assert (edge.subject_id, edge.object_id) == ("id-b-third", "id-c-primary"), "must stay ID-ordered"

    # Re-asserting via a synonym must update the canonical edge, not insert a parallel one.
    updated = set_relationship.execute(
        SetRelationshipInput(subject_id="id-c-primary", object_id="id-b-third", type="friend", label="close")
    )
    rows = SqliteRelationshipStore(conn).list_relationships()
    assert len(rows) == 1 and updated.id == edge.id and rows[0].label == "close"
