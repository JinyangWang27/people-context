"""Numbered review, selection by number, and `pctx import review --interactive` (M29.2).

Ordinals are a read-time convenience over staging order, so what is checked here is that they
behave like the ids they stand for: stable across edits, refused whole when any member is
unknown, and never shadowing a real candidate id. The interactive loop is driven through
`input()`, the same seam onboarding uses.
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db

_CANDIDATES = [
    {"type": "person", "ref": "p1", "name": "Nadia Okonkwo", "aliases": []},
    {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
    {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Lagos"},
    {"type": "affiliation", "person_ref": "p1", "org": "Globex", "role": "Advisor"},
    {"type": "fact", "person_ref": "p1", "predicate": "team", "value": "Platform"},
]


def _stage(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, list[str]]:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(_CANDIDATES), encoding="utf-8")
    code = cli.main(
        [
            "--db", str(db_file), "import", "stage-candidates",
            "--source", "review", "--input", str(path), "--source-kind", "notes", "--json",
        ]
    )
    assert code == 0
    batch_id = str(json.loads(capsys.readouterr().out)["batch_id"])
    return batch_id, [row["id"] for row in _review(db_file, batch_id, capsys)["candidates"]]


def _review(db_file: Path, batch_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    capsys.readouterr()
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    document: dict[str, object] = json.loads(capsys.readouterr().out)
    return document


def _statuses(db_file: Path) -> dict[str, str]:
    conn = open_db(db_file)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT id, status FROM import_staging")}
    finally:
        conn.close()


def _answers(monkeypatch: pytest.MonkeyPatch, *answers: str | Callable[[], str]) -> list[str]:
    """Feed `input()` from a script; a callable answer runs a side effect and returns its reply."""
    prompts: list[str] = []
    script: Iterator[str | Callable[[], str]] = iter(answers)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        try:
            answer = next(script)
        except StopIteration as exc:
            raise EOFError from exc
        return answer() if callable(answer) else answer

    monkeypatch.setattr(builtins, "input", fake_input)
    return prompts


def test_review_numbers_rows_and_summarizes_the_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, ids[4]]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "import", "review", batch_id]) == 0

    out = capsys.readouterr().out
    assert "Summary: 1 people (1 new, 0 matched, 0 ambiguous); 2 affiliation; 1 fact; 1 person; 1 withdrawn." in out
    assert f"#1  pending  person  Nadia Okonkwo  {ids[0]}" in out
    assert "#5  rejected  fact  " in out
    assert out.index("Summary:") < out.index("#1")


def test_ordinals_survive_amendment_and_withdrawal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    before = _review(db_file, batch_id, capsys)["candidates"]
    assert isinstance(before, list)
    assert [(row["ordinal"], row["id"]) for row in before] == list(enumerate(ids, start=1))

    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, ids[1]]) == 0
    assert cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[2], "--patch", '{"value": "Accra"}']) == 0
    after = _review(db_file, batch_id, capsys)["candidates"]

    assert isinstance(after, list)
    assert [(row["ordinal"], row["id"]) for row in after] == list(enumerate(ids, start=1))


def test_commit_accepts_a_mixed_selection_of_ordinals_ranges_and_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "commit", batch_id, "--accept", f"1,2-3,{ids[4]}", "--json"]
    )

    assert code == 0
    committed = json.loads(capsys.readouterr().out)["committed_ids"]
    assert sorted(committed) == sorted([ids[0], ids[1], ids[2], ids[4]])
    assert _statuses(db_file)[ids[3]] == "pending"


def test_commit_accepts_ordinals_as_separate_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--accept", "1", "4-5"]) == 0

    statuses = _statuses(db_file)
    assert [statuses[candidate_id] for candidate_id in ids] == [
        "committed", "pending", "pending", "committed", "committed"
    ]


@pytest.mark.parametrize("selection", ["1,9", "1,4-9", "1,3-2", "1,nope", "0"])
def test_one_unknown_member_refuses_the_whole_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], selection: str
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(["--db", str(db_file), "import", "commit", batch_id, "--accept", selection])

    assert code == 1
    captured = capsys.readouterr()
    assert "Unknown candidate IDs" in captured.err
    assert set(_statuses(db_file).values()) == {"pending"}


def test_a_candidate_id_spelled_like_an_ordinal_keeps_its_meaning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    # A restored batch may carry format-opaque ids. Moving this row to the end of staging order
    # makes its ordinal 5, so `1` could only mean the id or the person row at ordinal 1.
    conn = open_db(db_file)
    try:
        conn.execute(
            "UPDATE import_staging SET id = '1', created_at = '9999-12-31T00:00:00+00:00' WHERE id = ?",
            (ids[3],),
        )
        conn.commit()
    finally:
        conn.close()

    code = cli.main(["--db", str(db_file), "import", "commit", batch_id, "--accept", "1", "--json"])

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["committed_ids"] + result["unresolved_ids"] == ["1"]
    assert _statuses(db_file)[ids[0]] == "pending"


def test_interactive_quit_commits_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, _ids = _stage(db_file, tmp_path, capsys)
    _answers(monkeypatch, "a", "a", "q")

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--interactive"]) == 0

    assert "nothing committed" in capsys.readouterr().out
    assert set(_statuses(db_file).values()) == {"pending"}


def test_interactive_commits_exactly_the_accepted_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    _answers(monkeypatch, "a", "s", "w", "a", "s")

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--interactive"]) == 0

    statuses = _statuses(db_file)
    assert [statuses[candidate_id] for candidate_id in ids] == [
        "committed", "pending", "rejected", "committed", "pending"
    ]
    assert "Committed 2 candidates" in capsys.readouterr().out


def test_interactive_survives_an_invalid_edit_without_losing_acceptances(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    # Accept the person, try an invalid then a valid edit on the affiliation, accept it, skip the rest.
    _answers(monkeypatch, "a", "e", '{"type": "fact"}', "e", "not json", "e", '{"role": "Staff"}', "a", "s", "s", "s")

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--interactive"]) == 0

    statuses = _statuses(db_file)
    assert [statuses[candidate_id] for candidate_id in ids] == [
        "committed", "committed", "pending", "pending", "pending"
    ]
    assert "import review step failed" in capsys.readouterr().err
    document = _review(db_file, batch_id, capsys)
    candidates = document["candidates"]
    assert isinstance(candidates, list)
    assert candidates[1]["candidate"]["role"] == "Staff"


def test_interactive_restarts_when_the_batch_changes_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    def amend_accepted_row_elsewhere() -> str:
        code = cli.main(
            ["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", '{"role": "Changed"}', "--json"]
        )
        assert code == 0
        return "a"

    # Accept rows 1 and 2; row 2 is then amended by another client before the next action. The
    # refused action discards both acceptances, and the restarted loop accepts only row 1.
    _answers(monkeypatch, "a", "a", amend_accepted_row_elsewhere, "a", "s", "s", "s", "s")

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--interactive"]) == 0

    captured = capsys.readouterr()
    assert "Batch changed elsewhere; discarded 2 accepted candidates" in captured.err
    statuses = _statuses(db_file)
    assert [statuses[candidate_id] for candidate_id in ids] == [
        "committed", "pending", "pending", "pending", "pending"
    ]


def test_interactive_and_json_refuse_together(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch_id, _ids = _stage(db_file, tmp_path, capsys)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(db_file), "import", "review", batch_id, "--interactive", "--json"])

    assert exc.value.code == 2
