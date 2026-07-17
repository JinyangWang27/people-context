"""Tests for the `people-context` CLI (db-path, list, search, show, export)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app import AliasInput, RememberPerson, RememberPersonInput
from people_context.domain.person import Person
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
    assert "later milestones" in out


def test_show_by_unique_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    person = _seed(db_file, "Alice Wonderland")

    code = cli.main(["--db", str(db_file), "show", "Alice Wonderland"])
    assert code == 0
    out = capsys.readouterr().out
    assert person.id in out


def test_show_ambiguous_name_exits_2_and_lists_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
