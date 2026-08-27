"""`pctx import stage-candidates` — the agent extraction entry point and its input bounds.

These tests hold one line in particular: what the command accepts is an agent's distillation of
unstructured material, never the material itself. A transcript sentinel therefore appears in the
fixtures the agent "read" and must appear nowhere the command can reach.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import open_db
from people_context.app.imports import (
    CANDIDATE_INPUT_TOO_LARGE,
    IMPORT_BATCH_FORMAT,
    IMPORT_BATCH_VERSION,
    INVALID_CANDIDATE_JSON,
    MAX_CLI_CANDIDATE_JSON_BYTES,
    MAX_EXTRACTION_CANDIDATES,
    MAX_EXTRACTION_SOURCE_CHARS,
    MAX_EXTRACTION_STRING_BYTES,
)
from people_context.app.people import AliasInput, RememberPerson, RememberPersonInput
from people_context.cli.parser import build_parser
from people_context.domain.person import AliasKind

#: Stands in for the raw meeting transcript the agent read in its own environment.
_TRANSCRIPT_SENTINEL = "TRANSCRIPT-BODY-MUST-NOT-LEAK-7c3f"

_SOURCE_LABEL = "2026-08-27 planning sync"


def _candidates() -> list[dict[str, Any]]:
    """One batch shaped like a real transcript reading: people, then what was distilled."""
    return [
        {
            "type": "person",
            "ref": "sarah",
            "name": "Sarah Chen",
            "aliases": [{"kind": "handle", "value": "sarah@example.com"}],
        },
        {"type": "person", "ref": "bob", "name": "Bob Ali", "aliases": []},
        {
            "type": "observation",
            "person_ref": "sarah",
            "text": "Asked for concrete metrics before agreeing to the roadmap",
            "observed_at": "2026-08-27T10:00:00Z",
        },
        {
            "type": "trait",
            "person_ref": "sarah",
            "category": "communication_style",
            "value": "Responds better to proposals backed by quantitative evidence",
            "evidence_note": "Derived from the 27 Aug planning sync.",
            "confidence": 0.65,
        },
        {
            "type": "relationship",
            "from_ref": "sarah",
            "to_ref": "bob",
            "relationship_type": "manager",
        },
    ]


def _input_file(tmp_path: Path, candidates: Any, name: str = "candidates.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(candidates), encoding="utf-8")
    return path


def _stage(
    db_file: Path,
    input_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    source: str = _SOURCE_LABEL,
) -> dict[str, Any]:
    code = cli.main(
        ["--db", str(db_file), "import", "stage-candidates", "--source", source, "--input", str(input_path), "--json"]
    )
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def _staging_rows(db_file: Path) -> list[sqlite3.Row]:
    conn = open_db(db_file)
    try:
        return conn.execute("SELECT * FROM import_staging").fetchall()
    finally:
        conn.close()


def _refusal(db_file: Path, argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    code = cli.main(["--db", str(db_file), "import", "stage-candidates", *argv])
    assert code != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    return captured.err


def test_the_parser_requires_a_source_and_an_input() -> None:
    parser = build_parser()

    parsed = parser.parse_args(["import", "stage-candidates", "--source", "sync", "--input", "-"])
    assert parsed.import_command == "stage-candidates"
    assert (parsed.source, parsed.input, parsed.json) == ("sync", "-", False)

    for argv in (
        ["import", "stage-candidates", "--source", "sync"],
        ["import", "stage-candidates", "--input", "-"],
    ):
        with pytest.raises(SystemExit) as refusal:
            parser.parse_args(argv)
        assert refusal.value.code == 2


def test_the_source_label_is_free_form_rather_than_a_router_source_type() -> None:
    parsed = build_parser().parse_args(
        ["import", "stage-candidates", "--source", "a call with Dana", "--input", "notes.json"]
    )

    assert parsed.source == "a call with Dana"


def test_a_distilled_transcript_stages_reviews_and_commits(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    document = _stage(db_file, _input_file(tmp_path, _candidates()), capsys)
    assert document["format"] == IMPORT_BATCH_FORMAT
    assert document["version"] == IMPORT_BATCH_VERSION
    assert document["candidate_count"] == 5

    batch_id = str(document["batch_id"])
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    review = json.loads(capsys.readouterr().out)
    staged_types = [entry["candidate"]["type"] for entry in review["candidates"]]
    assert staged_types == ["person", "person", "observation", "trait", "relationship"]

    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all", "--json"]) == 0
    commit = json.loads(capsys.readouterr().out)
    assert len(commit["committed_ids"]) == 5
    assert commit["unresolved_ids"] == []

    assert cli.main(["--db", str(db_file), "show", "Sarah Chen"]) == 0
    shown = capsys.readouterr().out
    assert "manager" in shown
    assert "Bob Ali" in shown


def test_staging_alone_commits_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    code = cli.main(
        [
            "--db",
            str(db_file),
            "import",
            "stage-candidates",
            "--source",
            _SOURCE_LABEL,
            "--input",
            str(_input_file(tmp_path, _candidates())),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "nothing is committed yet" in output
    assert "pctx import review" in output
    assert all(row["status"] == "pending" for row in _staging_rows(db_file))


def test_stdin_input_is_equivalent_to_a_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "people.db"
    payload = json.dumps(_candidates()).encode("utf-8")
    monkeypatch.setattr(cli.imports.sys, "stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"))

    code = cli.main(
        ["--db", str(db_file), "import", "stage-candidates", "--source", _SOURCE_LABEL, "--input", "-", "--json"]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["candidate_count"] == 5


def test_an_extraction_batch_keeps_its_ambiguity_rather_than_inventing_a_person(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    # Two distinct people already carry the handle the candidate names. Its canonical name
    # matches exactly one of them, which must not be allowed to settle the question.
    runtime = build_runtime(db_file)
    try:
        remember: RememberPerson = runtime.use_cases.remember_person
        for name in ("Sarah Chen", "Sarah Chen-Okafor"):
            remember.execute(
                RememberPersonInput(
                    name=name,
                    aliases=[AliasInput(kind=AliasKind.HANDLE, value="sarah@example.com")],
                )
            )
    finally:
        runtime.close()

    document = _stage(db_file, _input_file(tmp_path, _candidates()), capsys)
    batch_id = str(document["batch_id"])

    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    review = json.loads(capsys.readouterr().out)
    sarah = next(entry for entry in review["candidates"] if entry["candidate"].get("name") == "Sarah Chen")
    assert sarah["candidate"]["match_disposition"] == "ambiguous"
    assert sarah["candidate"]["matched_person_id"] is None

    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all", "--json"]) == 0
    commit = json.loads(capsys.readouterr().out)
    assert sarah["id"] in commit["unresolved_ids"]


def test_the_transcript_itself_never_reaches_output_or_storage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    # The agent reads this in its own environment; the command is only ever given the distillation.
    (tmp_path / "meeting.md").write_text(f"# Planning sync\n\n{_TRANSCRIPT_SENTINEL}\n", encoding="utf-8")

    document = _stage(db_file, _input_file(tmp_path, _candidates()), capsys)
    batch_id = str(document["batch_id"])
    assert cli.main(["--db", str(db_file), "import", "review", batch_id]) == 0
    captured = capsys.readouterr()

    assert _TRANSCRIPT_SENTINEL not in captured.out
    assert _TRANSCRIPT_SENTINEL not in captured.err
    for row in _staging_rows(db_file):
        assert _TRANSCRIPT_SENTINEL not in row["candidate_json"]
        assert _TRANSCRIPT_SENTINEL not in row["source"]


def test_malformed_json_is_refused_without_echoing_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    broken = tmp_path / "broken.json"
    broken.write_text(f'[{{"type": "person", {_TRANSCRIPT_SENTINEL}', encoding="utf-8")

    message = _refusal(db_file, ["--source", "sync", "--input", str(broken)], capsys)

    assert INVALID_CANDIDATE_JSON in message
    assert _TRANSCRIPT_SENTINEL not in message
    assert _staging_rows(db_file) == []


def test_a_json_object_is_not_a_candidate_array(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    for payload in ({"type": "person"}, ["not an object"], 7):
        message = _refusal(
            db_file,
            ["--source", "sync", "--input", str(_input_file(tmp_path, payload))],
            capsys,
        )
        assert INVALID_CANDIDATE_JSON in message
    assert _staging_rows(db_file) == []


def test_an_unknown_candidate_type_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    payload = [{"type": "diagnosis", "person_ref": "sarah", "value": "anxious"}]

    message = _refusal(db_file, ["--source", "sync", "--input", str(_input_file(tmp_path, payload))], capsys)

    assert "candidate staging failed" in message
    assert _staging_rows(db_file) == []


def test_an_extra_field_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    payload = [
        {"type": "person", "ref": "sarah", "name": "Sarah Chen", "aliases": []},
        {
            "type": "relationship",
            "from_ref": "sarah",
            "to_ref": "sarah",
            "relationship_type": "manager",
            "sensitivity": "restricted",
        },
    ]

    message = _refusal(db_file, ["--source", "sync", "--input", str(_input_file(tmp_path, payload))], capsys)

    assert "candidate staging failed" in message
    assert _staging_rows(db_file) == []


def test_a_refused_batch_reports_where_it_failed_but_not_what_was_in_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    payload = [{"type": "person", "ref": "sarah", "name": _TRANSCRIPT_SENTINEL}]

    message = _refusal(db_file, ["--source", "sync", "--input", str(_input_file(tmp_path, payload))], capsys)

    assert "aliases" in message
    assert _TRANSCRIPT_SENTINEL not in message


def test_input_over_the_read_budget_is_refused_from_a_file_and_from_stdin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "people.db"
    oversized = b"[" + b'{"type":"person"},' * (MAX_CLI_CANDIDATE_JSON_BYTES // 18 + 1) + b"]"
    assert len(oversized) > MAX_CLI_CANDIDATE_JSON_BYTES
    path = tmp_path / "oversized.json"
    path.write_bytes(oversized)

    message = _refusal(db_file, ["--source", "sync", "--input", str(path)], capsys)
    assert CANDIDATE_INPUT_TOO_LARGE in message

    monkeypatch.setattr(cli.imports.sys, "stdin", io.TextIOWrapper(io.BytesIO(oversized), encoding="utf-8"))
    message = _refusal(db_file, ["--source", "sync", "--input", "-"], capsys)
    assert CANDIDATE_INPUT_TOO_LARGE in message

    assert _staging_rows(db_file) == []


def test_the_cli_bounds_count_source_and_strings_even_without_an_m17_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A path typed at a terminal is a weaker promise than an MCP caller's array.

    The MCP caps are conditional so that a released legacy batch is not narrowed under anyone.
    This boundary is new, so it has no such history to protect and bounds every request.
    """
    db_file = tmp_path / "people.db"
    legacy = [{"type": "person", "ref": f"r{index}", "name": f"N{index}", "aliases": []} for index in range(3)]

    too_many = [
        {"type": "person", "ref": f"r{index}", "name": f"N{index}", "aliases": []}
        for index in range(MAX_EXTRACTION_CANDIDATES + 1)
    ]
    assert "500" in _refusal(
        db_file, ["--source", "sync", "--input", str(_input_file(tmp_path, too_many))], capsys
    )

    long_label = "x" * (MAX_EXTRACTION_SOURCE_CHARS + 1)
    assert "128" in _refusal(
        db_file, ["--source", long_label, "--input", str(_input_file(tmp_path, legacy))], capsys
    )

    oversized_string = [
        {
            "type": "person",
            "ref": "sarah",
            "name": "Sarah Chen",
            "aliases": [],
            "summary": "s" * (MAX_EXTRACTION_STRING_BYTES + 1),
        }
    ]
    assert "8192" in _refusal(
        db_file, ["--source", "sync", "--input", str(_input_file(tmp_path, oversized_string))], capsys
    )

    assert _staging_rows(db_file) == []


def test_an_unreadable_input_path_is_refused_safely(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    message = _refusal(db_file, ["--source", "sync", "--input", str(tmp_path / "missing.json")], capsys)

    assert "cannot read" in message
    assert _staging_rows(db_file) == []
