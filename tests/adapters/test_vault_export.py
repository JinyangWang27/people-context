"""Integration tests for the safe deterministic Obsidian vault export."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.filesystem import MARKER_FILE, FileSystemVaultWriter
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    SqliteVaultReader,
    open_db,
)
from people_context.app import ExportVault, SetRelationship, SetRelationshipInput
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.vault import VaultPerson, VaultSnapshot
from people_context.ports.clock import SystemClock
from people_context.ports.vault import VaultSafetyError

_TS = "2026-01-01T00:00:00+00:00"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fixture(tmp_path: Path):
    db = tmp_path / "people.db"
    conn = open_db(db)
    people = SqlitePeopleRepository(conn)
    me = Person(canonical_name="Me", is_self=True, summary="The graph hub")
    ming = Person(
        canonical_name="小明",
        aliases=[Alias(value="Xiaoming", kind=AliasKind.TRANSLITERATION)],
        summary="Works on durable systems.",
    )
    manager = Person(canonical_name="Chen Wei")
    deleted = Person(canonical_name="Deleted Person", deleted_at=datetime.now(UTC))
    for person in (me, ming, manager, deleted):
        people.save_person(person)
    SetRelationship(
        people,
        SqliteRelationshipStore(conn),
        SqliteAuditLog(conn),
        SystemClock(),
        SqliteRelationshipVocabularyStore(conn),
    ).execute(SetRelationshipInput(subject_id=ming.id, object_id=manager.id, type="reports to"))
    with conn:
        conn.execute("INSERT INTO organizations (id, name, kind) VALUES ('org-1', 'Acme', 'company')")
        conn.execute(
            """
            INSERT INTO affiliations (
                id, person_id, org_id, role, valid_from, valid_to, confidence,
                provenance_source, provenance_session, provenance_stated_by, created_at
            ) VALUES ('aff-1', ?, 'org-1', 'Engineer', '2025-01-01', NULL, 1.0, 'test', NULL, NULL, ?)
            """,
            (ming.id, _TS),
        )
        conn.executemany(
            """
            INSERT INTO facts (
                id, person_id, predicate, value, valid_from, valid_to, recorded_at,
                confidence, sensitivity, provenance_source, provenance_session, provenance_stated_by
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, 1.0, ?, 'test', NULL, NULL)
            """,
            [
                ("fact-public", ming.id, "location", "Dubai", _TS, "public"),
                ("fact-sensitive", ming.id, "salary", "SECRET-SALARY", _TS, "sensitive"),
            ],
        )
        conn.execute(
            """
            INSERT INTO observations (
                id, person_id, text, observed_at, sensitivity,
                provenance_source, provenance_session, provenance_stated_by
            ) VALUES ('obs-1', ?, 'OBSERVATION-SENTINEL', ?, 'personal', 'test', NULL, NULL)
            """,
            (ming.id, _TS),
        )
        conn.execute(
            """
            INSERT INTO traits (
                id, person_id, category, value, evidence_note, confidence, sensitivity,
                provenance_source, provenance_session, provenance_stated_by, updated_at
            ) VALUES ('trait-1', ?, 'values', 'TRAIT-SENTINEL', NULL, 1.0, 'personal', 'test', NULL, NULL, ?)
            """,
            (ming.id, _TS),
        )
        conn.execute(
            """
            INSERT INTO interactions (
                id, summary, occurred_at, channel, sensitivity,
                provenance_source, provenance_session, provenance_stated_by
            ) VALUES ('interaction-1', 'INTERACTION-SENTINEL', ?, 'call', 'personal', 'test', NULL, NULL)
            """,
            (_TS,),
        )
        conn.execute(
            "INSERT INTO interaction_participants (interaction_id, person_id) VALUES ('interaction-1', ?)",
            (ming.id,),
        )
        conn.execute(
            """
            INSERT INTO reminders (id, person_id, text, kind, due_at, recurrence, status, created_at)
            VALUES ('reminder-1', ?, 'Send design notes', 'follow_up', NULL, NULL, 'active', ?)
            """,
            (ming.id, _TS),
        )
    return conn, ming, manager


def test_vault_layout_sensitivity_determinism_and_marked_regeneration(tmp_path: Path) -> None:
    conn, ming, _ = _fixture(tmp_path)
    output = tmp_path / "vault"
    exporter = ExportVault(SqliteVaultReader(conn), FileSystemVaultWriter())

    result = exporter.execute(output)
    first = _tree_bytes(output)
    assert result.people == 3
    assert result.organizations == 1
    assert (output / MARKER_FILE).is_file()
    assert (output / "People" / "小明.md").is_file()
    assert (output / "People" / "Me.md").is_file()
    assert not (output / "People" / "Deleted Person.md").exists()
    assert (output / "Organizations" / "Acme.md").is_file()

    note = (output / "People" / "小明.md").read_text(encoding="utf-8")
    assert '  - "Xiaoming"' in note
    assert "tags: [people-context/person]" in note
    assert f"people-context-id: {ming.id}" in note
    assert "- reports_to:: [[Chen Wei]]" in note
    assert "- role:: [[Acme]] — Engineer; active since 2025-01-01" in note
    assert "- location: Dubai" in note
    assert "- Send design notes" in note
    assert "SECRET-SALARY" not in note
    assert "OBSERVATION-SENTINEL" not in str(first)
    assert "TRAIT-SENTINEL" not in str(first)
    assert "INTERACTION-SENTINEL" not in str(first)

    exporter.execute(output)
    assert _tree_bytes(output) == first

    exporter.execute(output, include_sensitive=True)
    sensitive_note = (output / "People" / "小明.md").read_text(encoding="utf-8")
    assert "- salary: SECRET-SALARY" in sensitive_note

    with conn:
        conn.execute(
            """
            INSERT INTO facts (
                id, person_id, predicate, value, recorded_at, confidence, sensitivity, provenance_source
            ) VALUES ('fact-new', ?, 'timezone', 'Asia/Dubai', ?, 1.0, 'public', 'test')
            """,
            (ming.id, _TS),
        )
    exporter.execute(output)
    changed_note = (output / "People" / "小明.md").read_text(encoding="utf-8")
    assert "- timezone: Asia/Dubai" in changed_note
    assert "SECRET-SALARY" not in changed_note
    assert "- manages:: [[小明]]" in (output / "People" / "Chen Wei.md").read_text(encoding="utf-8")


def test_nonempty_unmarked_directory_is_refused_without_changes(tmp_path: Path) -> None:
    conn, _, _ = _fixture(tmp_path)
    output = tmp_path / "not-owned"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    before = _tree_bytes(output)

    with pytest.raises(VaultSafetyError, match="non-empty unmarked"):
        ExportVault(SqliteVaultReader(conn), FileSystemVaultWriter()).execute(output)

    assert _tree_bytes(output) == before


def test_filename_sanitization_preserves_cjk_and_suffixes_collisions(tmp_path: Path) -> None:
    snapshot = VaultSnapshot(
        people=[
            VaultPerson(id="01AAAA00000000000000000000", name="小明"),
            VaultPerson(id="01BBBB00000000000000000000", name="A/B"),
            VaultPerson(id="01CCCC00000000000000000000", name="A\\B"),
            VaultPerson(id="01DDDD00000000000000000000", name=".hidden"),
        ]
    )
    output = tmp_path / "vault"

    FileSystemVaultWriter().write_vault(output, snapshot)

    names = {path.name for path in (output / "People").iterdir()}
    assert "小明.md" in names
    assert "A_B (01BBBB).md" in names
    assert "A_B (01CCCC).md" in names
    assert "hidden.md" in names
