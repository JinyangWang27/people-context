"""CLI behaviour for `pctx group` management, reads (M28.1), and shared connections (M28.2)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import SqlitePeopleRepository, open_db
from people_context.app.groups.connections import SHARED_CONNECTIONS_FORMAT, SHARED_CONNECTIONS_VERSION
from people_context.app.groups.queries import GROUP_DETAIL_FORMAT, GROUP_DOCUMENT_VERSION, PERSON_MEMBERSHIPS_FORMAT
from people_context.app.relationships.commands import SetRelationshipInput
from people_context.cli.groups import GROUP_SENSITIVE_WARNING
from people_context.domain.person import Person


def _db(tmp_path: Path) -> Path:
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    try:
        repo = SqlitePeopleRepository(conn)
        for name in ("Alice Zhang", "Bob Li"):
            repo.save_person(Person(canonical_name=name))
    finally:
        conn.close()
    return db_file


def _json(db_file: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "group", *argv, "--json"]) == 0
    return dict(json.loads(capsys.readouterr().out))


def _history(db_file: Path) -> tuple[int, int]:
    conn = sqlite3.connect(db_file)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
    finally:
        conn.close()


def test_create_add_close_and_read_a_membership(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = _db(tmp_path)
    group = _json(db_file, capsys, "create", "Class 1, Grade 6", "--kind", "class")
    membership = _json(
        db_file,
        capsys,
        "add-member",
        group["id"],
        "Alice Zhang",
        "--role",
        "student",
        "--from",
        "2015-09-01",
        "--stated-by",
        "Alice",
    )
    closed = _json(db_file, capsys, "close-member", membership["id"], "--ended-on", "2016-07-15")

    before = _history(db_file)
    detail = _json(db_file, capsys, "show", group["id"])
    memberships = _json(db_file, capsys, "memberships", "Alice Zhang")

    assert closed["period"] == {"valid_from": "2015-09-01", "valid_to": "2016-07-15"}
    assert membership["provenance"] == {"source": "cli/group", "session": None, "stated_by": "Alice"}
    assert (detail["format"], detail["version"]) == (GROUP_DETAIL_FORMAT, GROUP_DOCUMENT_VERSION)
    assert [row["id"] for row in detail["memberships"]] == [membership["id"]]
    assert memberships["format"] == PERSON_MEMBERSHIPS_FORMAT
    assert _history(db_file) == before


def test_sensitive_output_needs_the_flag_and_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = _db(tmp_path)
    group = _json(db_file, capsys, "create", "Support circle", "--kind", "community", "--sensitivity", "sensitive")
    _json(db_file, capsys, "add-member", group["id"], "Alice Zhang")

    assert cli.main(["--db", str(db_file), "group", "show", group["id"]]) == 1
    assert "No group found" in capsys.readouterr().err
    assert _json(db_file, capsys, "memberships", "Alice Zhang")["memberships"] == []

    code = cli.main(["--db", str(db_file), "group", "memberships", "Alice Zhang", "--include-sensitive", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    assert GROUP_SENSITIVE_WARNING in captured.err
    assert [entry["group"]["group"]["name"] for entry in json.loads(captured.out)["memberships"]] == ["Support circle"]


def test_correct_distinguishes_an_error_from_a_closure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = _db(tmp_path)
    group = _json(db_file, capsys, "create", "Platform", "--kind", "team")
    membership = _json(
        db_file, capsys, "add-member", group["id"], "Bob Li", "--from", "2020-01-01", "--to", "2021-01-01"
    )

    assert cli.main(["--db", str(db_file), "group", "close-member", membership["id"], "--ended-on", "2022-01-01"]) == 2
    assert "already_ended" in capsys.readouterr().err

    corrected = _json(
        db_file, capsys, "correct", "membership", membership["id"], "--set", "valid_to=", "--set", "role=leader"
    )
    renamed = _json(db_file, capsys, "correct", "group", group["id"], "--set", "name=Platform Team")

    assert corrected["period"]["valid_to"] is None and corrected["role"] == "leader"
    assert renamed["name"] == "Platform Team"
    assert [row["group"]["id"] for row in _json(db_file, capsys, "find", "platform team")["groups"]] == [group["id"]]


@pytest.mark.parametrize(
    ("argv", "code", "message"),
    [
        (
            ["create", "X", "--kind", "class", "--organization", "01J0000000000000000000ORG1"],
            1,
            "organization not found",
        ),
        (["add-member", "01J0000000000000000000GRP1", "Alice Zhang"], 1, "group not found"),
        (["add-member", "01J0000000000000000000GRP1", "Nobody Here"], 1, "No person found"),
        (["find", "--limit", "0"], 2, "limit must be between"),
        (["correct", "group", "01J0000000000000000000GRP1", "--set", "nope"], 2, "FIELD=VALUE"),
    ],
)
def test_bad_input_exits_with_a_bounded_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], code: int, message: str
) -> None:
    db_file = _db(tmp_path)

    assert cli.main(["--db", str(db_file), "group", *argv]) == code

    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_a_rejected_basis_does_not_echo_submitted_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = _db(tmp_path)
    group = _json(db_file, capsys, "create", "Chess", "--kind", "club")

    code = cli.main(
        [
            "--db",
            str(db_file),
            "group",
            "add-member",
            group["id"],
            "Alice Zhang",
            "--basis",
            "unknown",
            "--from",
            "2015-01-01",
            "--stated-by",
            "private-attribution",
        ]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "cannot carry dates" in err
    assert "private-attribution" not in err


def test_shared_explains_classmates_in_json_and_text_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = _db(tmp_path)
    group = _json(db_file, capsys, "create", "Class 1, Grade 6", "--kind", "class")
    for name, end in (("Alice Zhang", "2016-06-30"), ("Bob Li", "2016-07-15")):
        _json(
            db_file, capsys, "add-member", group["id"], name, "--role", "student", "--from", "2015-09-01", "--to", end
        )
    hidden = _json(db_file, capsys, "create", "Support circle", "--kind", "community", "--sensitivity", "sensitive")
    for name in ("Alice Zhang", "Bob Li"):
        _json(db_file, capsys, "add-member", hidden["id"], name)
    before = _history(db_file)

    document = _json(db_file, capsys, "shared", "Alice Zhang", "Bob Li")
    assert cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Bob Li"]) == 0
    text = capsys.readouterr().out
    code = cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Bob Li", "--include-sensitive", "--json"])
    revealed = capsys.readouterr()

    assert (document["format"], document["version"]) == (SHARED_CONNECTIONS_FORMAT, SHARED_CONNECTIONS_VERSION)
    assert [(row["label"], row["temporal"]) for row in document["connections"]] == [("classmates", "overlap")]
    assert "classmates" in text and "2015-09-01..2016-06-30" in text
    assert code == 0 and GROUP_SENSITIVE_WARNING in revealed.err
    assert len(json.loads(revealed.out)["connections"]) == 2
    assert _history(db_file) == before


def test_shared_reports_no_groups_and_refuses_bad_people(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = _db(tmp_path)

    assert cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Bob Li"]) == 0
    assert "No shared groups found" in capsys.readouterr().out
    assert cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Nobody Here"]) == 1
    assert "No person found" in capsys.readouterr().err
    assert cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Alice Zhang"]) == 2
    assert "two different people" in capsys.readouterr().err


def test_shared_text_lists_direct_relationships_and_truncation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = _db(tmp_path)
    for name in ("Chess", "Go"):
        group = _json(db_file, capsys, "create", name, "--kind", "club")
        for person in ("Alice Zhang", "Bob Li"):
            _json(db_file, capsys, "add-member", group["id"], person)
    runtime = build_runtime(db_file)
    try:
        resolve = runtime.use_cases.resolve_person.execute
        alice, bob = (resolve(name).candidates[0].person_id for name in ("Alice", "Bob"))
        relationship = SetRelationshipInput(subject_id=alice, object_id=bob, type="friend_of")
        runtime.use_cases.set_relationship.execute(relationship)
    finally:
        runtime.close()

    assert cli.main(["--db", str(db_file), "group", "shared", "Alice Zhang", "Bob Li", "--limit", "1"]) == 0

    out = capsys.readouterr().out
    assert "Chess" in out and "Go  " not in out
    assert "More connections exist" in out
    assert "Direct relationships:" in out
