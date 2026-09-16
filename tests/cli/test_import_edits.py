"""`pctx import amend` and `pctx import reject` at the CLI process boundary (M29.1).

The rules themselves live with the use cases. What is checked here is the boundary around them:
how a patch is read, what `--json` emits, what a refusal prints to stderr while leaving stdout
free of a partial document, and that `--all` still means what it meant.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db
from people_context.app.imports import (
    CANDIDATE_INPUT_TOO_LARGE,
    IMPORT_REVIEW_FORMAT,
    IMPORT_REVIEW_VERSION,
    INVALID_CANDIDATE_JSON,
    MAX_CLI_CANDIDATE_JSON_BYTES,
    ImportBatchResult,
)
from people_context.cli import imports as cli_imports

_CANDIDATES = [
    {"type": "person", "ref": "p1", "name": "Nadia Okonkwo", "aliases": []},
    {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
]


def _input(tmp_path: Path, candidates: list[dict[str, object]] | None = None) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(candidates if candidates is not None else _CANDIDATES), encoding="utf-8")
    return path


def _stage(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[str, list[str]]:
    code = cli.main(
        [
            "--db", str(db_file), "import", "stage-candidates",
            "--source", "review", "--input", str(_input(tmp_path)),
            "--source-kind", "notes", "--json",
        ]
    )
    assert code == 0
    batch_id = str(json.loads(capsys.readouterr().out)["batch_id"])
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    review = json.loads(capsys.readouterr().out)
    return batch_id, [row["id"] for row in review["candidates"]]


class _Stdin:
    """Just enough of `sys.stdin` for the byte-bounded read the command performs."""

    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)


def _stdin(raw: bytes) -> _Stdin:
    return _Stdin(raw)


def _statuses(db_file: Path) -> list[tuple[str, str]]:
    conn = open_db(db_file)
    try:
        conn.row_factory = sqlite3.Row
        return [
            (row["id"], row["status"])
            for row in conn.execute("SELECT id, status FROM import_staging ORDER BY created_at, id")
        ]
    finally:
        conn.close()


def test_amend_prints_the_whole_refreshed_review_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", '{"role": "Staff Engineer"}', "--json"]
    )

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == IMPORT_REVIEW_FORMAT
    assert document["version"] == IMPORT_REVIEW_VERSION
    assert [row["id"] for row in document["candidates"]] == ids
    assert document["candidates"][1]["candidate"]["role"] == "Staff Engineer"
    assert document["batch_digest"]


def test_amend_reads_a_patch_from_stdin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    monkeypatch.setattr("sys.stdin", _stdin(b'{"role": "Principal"}'))

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", "-", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["candidates"][1]["candidate"]["role"] == "Principal"


def test_amend_prints_the_disclosure_warning_and_the_batch_in_human_form(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", '{"role": "Staff Engineer"}']
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "Staff Engineer" in captured.out
    assert f"pctx import commit {batch_id} --all" in captured.out
    assert "Warning:" in captured.err


@pytest.mark.parametrize(
    "patch",
    ["not json", "[]", '{"type": "fact"}', '{"role": ""}'],
)
def test_a_refused_amendment_leaves_stdout_empty_and_the_row_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], patch: str
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", patch, "--json"])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Error:")
    assert _statuses(db_file) == [(ids[0], "pending"), (ids[1], "pending")]


def test_a_refusal_never_prints_an_undeclared_patch_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    private = "Nadia is being treated at the Mayo Clinic"

    code = cli.main(
        ["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", json.dumps({private: "x"})]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert private not in captured.err
    assert "(redacted)" in captured.err


def test_reject_withdraws_several_candidates_at_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(["--db", str(db_file), "import", "reject", batch_id, *ids, "--json"])

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in document["candidates"]] == ["rejected", "rejected"]


def test_a_withdrawn_candidate_stays_listed_by_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, ids[1]]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in document["candidates"]] == ["pending", "rejected"]


def test_commit_all_skips_a_withdrawn_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, ids[1]]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["committed_ids"] == [ids[0]]
    assert document["unresolved_ids"] == []


def test_naming_a_withdrawn_candidate_in_accept_refuses_the_whole_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, ids[1]]) == 0
    capsys.readouterr()

    code = cli.main(
        ["--db", str(db_file), "import", "commit", batch_id, "--accept", ids[0], "--accept", ids[1], "--json"]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "withdrawn" in captured.err
    assert _statuses(db_file) == [(ids[0], "pending"), (ids[1], "rejected")]


def test_a_refused_rejection_leaves_stdout_empty_and_every_row_pending(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "reject", batch_id, ids[0], "01JUNKNOTINTHISBATCH000000", "--json"]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("Error: import reject failed:")
    assert _statuses(db_file) == [(ids[0], "pending"), (ids[1], "pending")]


def test_rejecting_every_candidate_leaves_the_receipt_terminally_withdrawn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, *ids]) == 0
    capsys.readouterr()

    assert cli.main(["--db", str(db_file), "sources", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert [source["status"] for source in document["sources"]] == ["withdrawn"]


def test_amending_an_unknown_batch_refuses_rather_than_creating_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _stage(db_file, tmp_path, capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "amend", "01JUNKNOTABATCH0000000000", "c1", "--patch", "{}"]
    )

    assert code == 1
    assert capsys.readouterr().out == ""


def test_reimporting_a_withdrawn_source_does_not_call_its_candidates_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: a fully withdrawn source reported durable records it never produced.

    `reviewable` alone cannot tell the two terminal states apart, so the duplicate report read
    every finished batch as a committed one.
    """
    db_file = tmp_path / "people.db"
    digest = "a" * 64
    stage = [
        "--db", str(db_file), "import", "stage-candidates",
        "--source", "review", "--input", str(_input(tmp_path)),
        "--source-kind", "notes", "--content-digest", digest,
    ]
    assert cli.main([*stage, "--json"]) == 0
    batch_id = str(json.loads(capsys.readouterr().out)["batch_id"])
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    ids = [row["id"] for row in json.loads(capsys.readouterr().out)["candidates"]]
    assert cli.main(["--db", str(db_file), "import", "reject", batch_id, *ids]) == 0
    capsys.readouterr()

    assert cli.main(stage) == 0

    out = capsys.readouterr().out
    assert "withdrawn candidates" in out
    assert "committed" not in out


def test_reimporting_a_committed_source_still_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    digest = "b" * 64
    stage = [
        "--db", str(db_file), "import", "stage-candidates",
        "--source", "review", "--input", str(_input(tmp_path)),
        "--source-kind", "notes", "--content-digest", digest,
    ]
    assert cli.main([*stage, "--json"]) == 0
    batch_id = str(json.loads(capsys.readouterr().out)["batch_id"])
    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all"]) == 0
    capsys.readouterr()

    assert cli.main(stage) == 0

    assert "committed candidates" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reviewable", "status", "expected_held"),
    [
        (True, "staged", "candidates"),
        (False, "committed", "committed candidates"),
        (False, "withdrawn", "withdrawn candidates"),
        (False, None, "candidates"),
    ],
)
def test_the_duplicate_report_describes_each_terminal_state_truthfully(
    reviewable: bool, status: str | None, expected_held: str
) -> None:
    """A batch from a bundle may carry no receipt status at all, and must not be guessed at."""
    batch = ImportBatchResult(
        batch_id="01J0000000000000000BATCH01",
        candidate_count=1,
        duplicate=True,
        reviewable=reviewable,
        source_status=status,
    )

    held, outcome = cli_imports._duplicate_wording(batch)

    assert held == expected_held
    assert (outcome is None) is reviewable
    if outcome is not None and status != "committed":
        assert "committed" not in outcome


def test_an_oversized_patch_on_stdin_is_refused_before_it_is_decoded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling is spent on the read: a pipe has no size to stat, so nothing is buffered whole."""
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    monkeypatch.setattr("sys.stdin", _stdin(b"x" * (MAX_CLI_CANDIDATE_JSON_BYTES + 10)))

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", "-"])

    assert code == 1
    assert CANDIDATE_INPUT_TOO_LARGE in capsys.readouterr().err


def test_a_patch_on_stdin_that_is_not_utf8_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    monkeypatch.setattr("sys.stdin", _stdin(b'{"role": "\xff\xfe"}'))

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", "-"])

    assert code == 1
    assert "not valid UTF-8" in capsys.readouterr().err


def test_an_oversized_inline_patch_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    oversized = json.dumps({"role": "x" * (MAX_CLI_CANDIDATE_JSON_BYTES + 10)})

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", oversized])

    assert code == 1
    assert CANDIDATE_INPUT_TOO_LARGE in capsys.readouterr().err


def test_a_patch_nested_too_deeply_is_refused_as_unparseable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The decoder recurses per level, so depth exhausts the stack far below the byte ceiling."""
    db_file = tmp_path / "people.db"
    batch_id, ids = _stage(db_file, tmp_path, capsys)
    deep = "[" * 40_000 + "]" * 40_000

    code = cli.main(["--db", str(db_file), "import", "amend", batch_id, ids[1], "--patch", deep])

    assert code == 1
    assert INVALID_CANDIDATE_JSON in capsys.readouterr().err
