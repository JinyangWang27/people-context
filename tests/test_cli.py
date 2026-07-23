"""Tests for the `people-context` CLI (db-path, list, search, show, export)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, SqlitePreferencesStore, open_db
from people_context.app.people import AliasInput, RememberPerson, RememberPersonInput
from people_context.cli import maintenance as maintenance_module
from people_context.domain.person import Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.ports.clock import SystemClock


def _seed(db_path: Path, name: str, **kwargs: object) -> Person:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        remember = RememberPerson(repo, repo, SqliteAuditLog(conn), SystemClock())
        result = remember.execute(RememberPersonInput(name=name, **kwargs))  # type: ignore[arg-type]
        return result.person
    finally:
        conn.close()


def _soft_delete(db_path: Path, person_id: str) -> None:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        person = repo.get(person_id)
        assert person is not None
        person.deleted_at = SystemClock().now()
        repo.save_person(person)
    finally:
        conn.close()


# -- db-path ------------------------------------------------------------------


def test_db_path_prints_resolved_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    code = cli.main(["--db", str(db_file), "db-path"])
    assert code == 0
    assert capsys.readouterr().out.strip() == str(db_file)


def test_db_path_verbose_shows_resolution_trace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    code = cli.main(["--db", str(db_file), "db-path", "-v"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[WON ]" in out
    assert "explicit argument" in out
    assert f"=> resolved: {db_file}" in out
    assert not db_file.exists()  # db-path must never open the database


# -- list -----------------------------------------------------------------


def test_list_shows_seeded_person_and_hides_deleted_unless_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    live = _seed(db_file, "Alice Wonderland", summary="A curious person from the story.")
    gone = _seed(db_file, "Mad Hatter")
    _soft_delete(db_file, gone.id)

    code = cli.main(["--db", str(db_file), "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Alice Wonderland" in out
    assert live.id in out
    assert "Mad Hatter" not in out

    code = cli.main(["--db", str(db_file), "list", "--all"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Alice Wonderland" in out
    assert "Mad Hatter [deleted]" in out


# -- search -----------------------------------------------------------------


def test_search_finds_partial_name_with_score(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice Wonderland")

    code = cli.main(["--db", str(db_file), "search", "alice"])
    assert code == 0
    out = capsys.readouterr().out
    assert person.id in out
    assert "Alice Wonderland" in out
    # A score line looks like "0.90  Alice Wonderland  (<id>)  search:canonical"
    score_field = out.split()[0]
    assert float(score_field) > 0.0


# -- show -------------------------------------------------------------------


def test_show_by_exact_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice Wonderland", summary="A curious person.")

    code = cli.main(["--db", str(db_file), "show", person.id])
    assert code == 0
    out = capsys.readouterr().out
    assert f"Alice Wonderland ({person.id})" in out
    assert "summary: A curious person." in out
    assert "relationships:" in out
    assert "affiliations:" in out
    assert "facts:" in out
    assert "interactions:" in out
    assert "communication reminders:" in out


def test_show_by_unique_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice Wonderland")

    code = cli.main(["--db", str(db_file), "show", "Alice Wonderland"])
    assert code == 0
    out = capsys.readouterr().out
    assert person.id in out


def test_show_ambiguous_name_exits_2_and_lists_candidates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    first = _seed(db_file, "Alice Smith", aliases=[AliasInput(value="Ally")])
    second = _seed(db_file, "Alice Jones", aliases=[AliasInput(value="Ally")])

    code = cli.main(["--db", str(db_file), "show", "Ally"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Ambiguous match" in err
    assert first.id in err
    assert second.id in err


def test_show_unknown_person_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file, "Alice Wonderland")

    code = cli.main(["--db", str(db_file), "show", "Nobody Like This Exists At All"])
    assert code == 1
    err = capsys.readouterr().err
    assert "No person found" in err


# -- export -----------------------------------------------------------------


def test_export_to_stdout_includes_seeded_person(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice Wonderland")

    code = cli.main(["--db", str(db_file), "export"])
    assert code == 0
    out = capsys.readouterr().out
    document = json.loads(out)
    assert document["format"] == "people-context-export"
    assert document["version"] == 1
    assert "exported_at" in document
    ids = {p["id"] for p in document["people"]}
    assert person.id in ids


def test_export_to_file_includes_soft_deleted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    live = _seed(db_file, "Alice Wonderland")
    gone = _seed(db_file, "Mad Hatter")
    _soft_delete(db_file, gone.id)
    output_file = tmp_path / "export.json"

    code = cli.main(["--db", str(db_file), "export", "--output", str(output_file)])
    assert code == 0
    capsys.readouterr()  # nothing printed to stdout
    document = json.loads(output_file.read_text(encoding="utf-8"))
    ids = {p["id"] for p in document["people"]}
    assert live.id in ids
    assert gone.id in ids


# -- sync log ---------------------------------------------------------------


def test_sync_log_hides_payloads_by_default_and_can_filter_and_reveal_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    sentinel = "SYNC-LOG-SENTINEL-42"
    person = _seed(db_file, "Alice", summary=sentinel)

    assert cli.main(["--db", str(db_file), "sync-log", "--limit", "1"]) == 0
    hidden = capsys.readouterr().out
    assert f"create  person:{person.id}" in hidden
    assert "device=" in hidden and "hlc=" in hidden and "fields=-" in hidden
    assert "payload=" not in hidden
    assert sentinel not in hidden

    assert cli.main(
        ["--db", str(db_file), "sync-log", "--entity", person.id, "--payloads"]
    ) == 0
    revealed = capsys.readouterr().out
    assert f"create  person:{person.id}" in revealed
    assert "payload=" in revealed
    assert sentinel in revealed


# -- curation ---------------------------------------------------------------


def test_edit_requires_a_field_updates_person_and_audits_before_after(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice", summary="Old")

    assert cli.main(["--db", str(db_file), "edit", person.id]) == 2
    assert "requires" in capsys.readouterr().err
    assert cli.main(
        ["--db", str(db_file), "edit", person.id, "--name", "Alice Smith", "--summary", "New"]
    ) == 0

    conn = open_db(db_file)
    try:
        updated = SqlitePeopleRepository(conn).get(person.id)
        entry = next(item for item in SqliteAuditLog(conn).list_entries() if item.entity_id == person.id)
    finally:
        conn.close()
    assert updated is not None
    assert updated.canonical_name == "Alice Smith"
    assert entry.payload["before"]["canonical_name"] == "Alice"
    assert entry.payload["after"]["summary"] == "New"
    assert entry.payload["fields"] == ["canonical_name", "summary"]


def test_edit_rejects_canonical_collision_and_shared_resolver_keeps_ambiguous_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    alice = _seed(db_file, "Alice")
    _seed(db_file, "Bob")
    first = _seed(db_file, "Ally One", aliases=[AliasInput(value="Ally")])
    second = _seed(db_file, "Ally Two", aliases=[AliasInput(value="Ally")])

    assert cli.main(["--db", str(db_file), "edit", alice.id, "--name", "bob"]) == 1
    assert "already belongs" in capsys.readouterr().err
    assert cli.main(["--db", str(db_file), "add-alias", "Ally", "Alias"]) == 2
    err = capsys.readouterr().err
    assert first.id in err and second.id in err


def test_add_alias_and_set_preference_use_application_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice")

    assert cli.main(
        ["--db", str(db_file), "add-alias", person.id, "Ally", "--kind", "nickname", "--lang", "en"]
    ) == 0
    assert cli.main(["--db", str(db_file), "set", "unsupported", "x"]) == 2
    assert "Unsupported" in capsys.readouterr().err
    assert cli.main(
        ["--db", str(db_file), "set", PREF_COMMUNICATION_PHILOSOPHY, "Prefer concise updates"]
    ) == 0

    conn = open_db(db_file)
    try:
        updated = SqlitePeopleRepository(conn).get(person.id)
        philosophy = SqlitePreferencesStore(conn).get(PREF_COMMUNICATION_PHILOSOPHY)
    finally:
        conn.close()
    assert updated is not None
    assert [(alias.value, alias.kind.value, alias.lang) for alias in updated.aliases] == [
        ("Ally", "nickname", "en")
    ]
    assert philosophy == "Prefer concise updates"


def test_delete_abort_is_non_mutating_and_confirmation_forgets_person(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice")
    monkeypatch.setattr("builtins.input", lambda _: "no")

    assert cli.main(["--db", str(db_file), "delete", person.id]) == 0
    assert "Aborted." in capsys.readouterr().out
    conn = open_db(db_file)
    try:
        assert SqlitePeopleRepository(conn).get(person.id) is not None
    finally:
        conn.close()

    assert cli.main(["--db", str(db_file), "delete", person.id, "--yes"]) == 0
    assert "Deleted." in capsys.readouterr().out
    conn = open_db(db_file)
    try:
        assert SqlitePeopleRepository(conn).get(person.id) is None
    finally:
        conn.close()


def test_reindex_restores_search_after_manual_fts_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice", aliases=[AliasInput(value="Ally")])
    conn = open_db(db_file)
    try:
        with conn:
            conn.execute("DELETE FROM person_search WHERE person_id = ?", (person.id,))
    finally:
        conn.close()

    assert cli.main(["--db", str(db_file), "reindex"]) == 0
    assert "2 names" in capsys.readouterr().out
    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM person_search WHERE person_id = ?", (person.id,)).fetchone()[0] == 2
    finally:
        conn.close()
    assert cli.main(["--db", str(db_file), "search", "Ally"]) == 0
    assert person.id in capsys.readouterr().out


def test_semantic_reindex_is_explicit_and_preserves_metadata_on_download_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file, "Alice")
    conn = open_db(db_file)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO user_preferences (key, value_json, updated_at) VALUES (?, ?, ?)",
                [
                    ("semantic_embedding_model_id", '"prior/model"', "2026-01-01T00:00:00+00:00"),
                    ("semantic_embedding_dimension", "256", "2026-01-01T00:00:00+00:00"),
                ],
            )
    finally:
        conn.close()

    def fail_download() -> None:
        raise OSError("offline")

    monkeypatch.setattr(maintenance_module, "download_embedding_provider", fail_download)

    assert cli.main(["--db", str(db_file), "reindex", "--semantic"]) == 1
    captured = capsys.readouterr()
    assert "minishlab/potion-multilingual-128M@73908c" in captured.out
    assert "approximately 512 MB" in captured.out
    assert "Cache directory:" in captured.out
    assert "offline" in captured.err
    conn = open_db(db_file)
    try:
        stored = conn.execute(
            "SELECT value_json FROM user_preferences WHERE key = 'semantic_embedding_model_id'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert stored == '"prior/model"'
