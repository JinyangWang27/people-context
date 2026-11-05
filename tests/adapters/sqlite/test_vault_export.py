"""Integration tests for the safe deterministic Obsidian vault export."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.filesystem import MARKER_FILE, FileSystemVaultWriter
from people_context.adapters.filesystem.vault_writer import sanitize_filename
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    SqliteVaultReader,
    open_db,
)
from people_context.app.exports import ExportVault
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.vault import VaultPerson, VaultSnapshot
from people_context.ports.clock import SystemClock
from people_context.ports.vault import VaultSafetyError

_TS = "2026-01-01T00:00:00+00:00"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


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
    exporter = ExportVault(SqliteVaultReader(conn, SystemClock()), FileSystemVaultWriter())

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
        ExportVault(SqliteVaultReader(conn, SystemClock()), FileSystemVaultWriter()).execute(output)

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


def test_marked_vault_regeneration_preserves_obsidian_and_user_paths(tmp_path: Path) -> None:
    output = tmp_path / "vault"
    writer = FileSystemVaultWriter()
    initial = VaultSnapshot(people=[VaultPerson(id="01AAAA00000000000000000000", name="Alice")])
    writer.write_vault(output, initial)
    generated_before = {
        path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()
    }
    obsidian = output / ".obsidian" / "workspace.json"
    obsidian.parent.mkdir()
    obsidian.write_text('{"layout":"user-owned"}\n', encoding="utf-8")
    user_note = output / "Notes" / "my-note.md"
    user_note.parent.mkdir()
    user_note.write_text("# Keep me\n", encoding="utf-8")
    scratch = output / "scratch.md"
    scratch.write_text("user root note\n", encoding="utf-8")
    stale_generated = output / "People" / "stale.md"
    stale_generated.write_text("stale\n", encoding="utf-8")

    writer.write_vault(output, initial)

    for relative, content in generated_before.items():
        assert (output / relative).read_bytes() == content
    assert (output / MARKER_FILE).is_file()
    assert obsidian.read_text(encoding="utf-8") == '{"layout":"user-owned"}\n'
    assert user_note.read_text(encoding="utf-8") == "# Keep me\n"
    assert scratch.read_text(encoding="utf-8") == "user root note\n"
    assert not stale_generated.exists()


def test_email_addresses_are_listed_as_emails_rather_than_obsidian_aliases(tmp_path: Path) -> None:
    person = VaultPerson(id="p1", name="Jordan Lee", aliases=["Jordy"], emails=["jordan@example.com"])
    output = tmp_path / "vault"

    FileSystemVaultWriter().write_vault(output, VaultSnapshot(people=[person], organizations=[]))

    note = (output / "People" / "Jordan Lee.md").read_text(encoding="utf-8")
    front = note.split("---")[1]
    assert front.index("aliases:") < front.index('  - "Jordy"') < front.index("emails:")
    assert front.index("emails:") < front.index('  - "jordan@example.com"')
