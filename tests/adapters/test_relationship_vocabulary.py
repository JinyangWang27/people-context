"""M7 relationship vocabulary, canonicalization, and migration tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteContextReader,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app import NormalizeRelationships, SetRelationship, SetRelationshipInput
from people_context.domain.person import Person
from people_context.ports.clock import SystemClock


def _people(conn: sqlite3.Connection) -> tuple[Person, Person]:
    repo = SqlitePeopleRepository(conn)
    a = Person(canonical_name="A")
    b = Person(canonical_name="B")
    repo.save_person(a)
    repo.save_person(b)
    return a, b


def test_fresh_and_m6_database_apply_pending_migrations(tmp_path: Path) -> None:
    fresh = open_db(":memory:")
    assert fresh.execute("PRAGMA user_version").fetchone()[0] == 4
    assert fresh.execute("SELECT COUNT(*) FROM relationship_types").fetchone()[0] == 14
    assert fresh.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0

    path = tmp_path / "m6.db"
    legacy = sqlite3.connect(path)
    migrations = resources.files("people_context.adapters.sqlite.migrations")
    legacy.executescript(migrations.joinpath("001_initial.sql").read_text(encoding="utf-8"))
    legacy.executescript(migrations.joinpath("002_sync_foundations.sql").read_text(encoding="utf-8"))
    legacy.execute("PRAGMA user_version = 2")
    legacy.commit()
    legacy.close()

    upgraded = open_db(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 4
    inverse = upgraded.execute(
        "SELECT inverse FROM relationship_types WHERE type = 'reports_to'"
    ).fetchone()[0]
    assert inverse == "manages"
    assert upgraded.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0


def test_set_relationship_normalizes_deduplicates_and_renders_perspective() -> None:
    conn = open_db(":memory:")
    a, b = _people(conn)
    store = SqliteRelationshipStore(conn)
    vocabulary = SqliteRelationshipVocabularyStore(conn)
    audit = SqliteAuditLog(conn)
    use_case = SetRelationship(SqlitePeopleRepository(conn), store, audit, SystemClock(), vocabulary)

    first = use_case.execute(
        SetRelationshipInput(subject_id=b.id, object_id=a.id, type="manager of", label="first")
    )
    second = use_case.execute(
        SetRelationshipInput(subject_id=a.id, object_id=b.id, type="reports to", label="updated")
    )

    assert second.id == first.id
    assert (second.subject_id, second.object_id, second.type, second.label) == (
        a.id,
        b.id,
        "reports_to",
        "updated",
    )
    assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 1
    assert [entry.op for entry in audit.list_entries()] == ["update", "create"]
    changelog = SqliteChangelog(conn).list_entries(limit=10)
    assert [entry.op_kind for entry in changelog] == ["update", "create"]
    assert all(entry.payload["type"] == "reports_to" for entry in changelog)

    reader = SqliteContextReader(conn)
    assert reader.list_active_relationships(a.id, datetime.now(UTC).date())[0].display_type == "reports_to"
    assert reader.list_active_relationships(b.id, datetime.now(UTC).date())[0].display_type == "manages"


def test_symmetric_and_unknown_relationship_types_round_trip() -> None:
    conn = open_db(":memory:")
    a, b = _people(conn)
    store = SqliteRelationshipStore(conn)
    vocabulary = SqliteRelationshipVocabularyStore(conn)
    use_case = SetRelationship(
        SqlitePeopleRepository(conn),
        store,
        SqliteAuditLog(conn),
        SystemClock(),
        vocabulary,
    )

    first = use_case.execute(SetRelationshipInput(subject_id=b.id, object_id=a.id, type="friend of"))
    repeated = use_case.execute(SetRelationshipInput(subject_id=a.id, object_id=b.id, type="friend_of", label="x"))
    unknown = use_case.execute(
        SetRelationshipInput(subject_id=a.id, object_id=b.id, type="Childhood Rival Of")
    )

    assert repeated.id == first.id
    assert (first.subject_id, first.object_id) == tuple(sorted((a.id, b.id)))
    assert first.type == "friend_of"
    assert unknown.type == "childhood_rival_of"
    assert vocabulary.list_uncategorized_types() == ["childhood_rival_of"]
    context = SqliteContextReader(conn).list_active_relationships(a.id, datetime.now(UTC).date())
    assert {row.display_type for row in context} == {"friend_of", "childhood_rival_of"}


def test_normalize_relationships_dry_run_and_apply_audit_every_rewrite() -> None:
    conn = open_db(":memory:")
    a, b = _people(conn)
    older = "01J00000000000000000000001"
    newer = "01J00000000000000000000002"
    with conn:
        conn.executemany(
            """
            INSERT INTO relationships (
                id, subject_id, object_id, type, label, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, 1.0, 'legacy', NULL, NULL, ?)
            """,
            [
                (older, b.id, a.id, "manages", "2025-01-01T00:00:00+00:00"),
                (newer, a.id, b.id, "reports_to", "2025-01-02T00:00:00+00:00"),
            ],
        )
    audit = SqliteAuditLog(conn)
    use_case = NormalizeRelationships(
        SqliteRelationshipStore(conn),
        SqliteRelationshipVocabularyStore(conn),
        audit,
        SystemClock(),
    )

    dry = use_case.execute()
    assert [(change.action, change.relationship_id) for change in dry.changes] == [
        ("update", older),
        ("merge", newer),
    ]
    assert conn.execute("SELECT type FROM relationships WHERE id = ?", (older,)).fetchone()[0] == "manages"
    assert audit.list_entries() == []

    applied = use_case.execute(apply=True)
    assert applied.applied is True
    rows = conn.execute("SELECT id, subject_id, object_id, type FROM relationships").fetchall()
    assert [(row["id"], row["subject_id"], row["object_id"], row["type"]) for row in rows] == [
        (older, a.id, b.id, "reports_to")
    ]
    assert [entry.op for entry in audit.list_entries()] == ["delete", "update"]
    changelog = SqliteChangelog(conn).list_entries(limit=10)
    assert [entry.op_kind for entry in changelog] == ["delete", "update"]
    assert changelog[1].payload["type"] == "reports_to"
