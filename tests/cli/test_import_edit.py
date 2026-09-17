"""`pctx import edit` at the CLI process boundary (M29.3).

The editor is a small Python script named through `$EDITOR`, so the round trip runs a real
subprocess without a shell. The commit prompt reads the controlling terminal, which a test does
not have, so `_confirm_on_terminal` is replaced where a test needs an answer.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import open_db
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.cli import imports as cli_imports
from people_context.domain.person import Person

_NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)

_CANDIDATES = [
    {"type": "person", "ref": "p1", "name": "Nadia Okonkwo", "aliases": []},
    {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
    {"type": "fact", "person_ref": "p1", "predicate": "likes", "value": "tea"},
]


@pytest.fixture(autouse=True)
def _no_terminal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail loudly if a path that must not prompt does; record the prompts that are allowed."""
    prompts: list[str] = []
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    def refuse(prompt: str) -> bool:
        prompts.append(prompt)
        return False

    monkeypatch.setattr(cli_imports, "_confirm_on_terminal", refuse)
    return prompts


def _stage(db_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], candidates: Any = None) -> str:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(candidates or _CANDIDATES), encoding="utf-8")
    code = cli.main(
        [
            "--db", str(db_file), "import", "stage-candidates", "--source", "review",
            "--input", str(path), "--source-kind", "notes", "--json",
        ]
    )
    assert code == 0
    return str(json.loads(capsys.readouterr().out)["batch_id"])


def _review(db_file: Path, batch_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    return dict(json.loads(capsys.readouterr().out))


def _rows(db_file: Path) -> list[dict[str, Any]]:
    conn = open_db(db_file)
    try:
        conn.row_factory = sqlite3.Row
        return [
            {"id": row["id"], "status": row["status"], "candidate": json.loads(row["candidate_json"])}
            for row in conn.execute("SELECT * FROM import_staging ORDER BY created_at, id")
        ]
    finally:
        conn.close()


def _editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, exit_code: int = 0) -> Path:
    """Point `$EDITOR` at a script that runs `body` over the parsed document as `doc`."""
    record = tmp_path / "editor-record.json"
    script = tmp_path / "editor.py"
    script.write_text(
        "import json, os, sys\n"
        "path = sys.argv[1]\n"
        "doc = json.load(open(path, encoding='utf-8'))\n"
        f"{body}\n"
        "open(path, 'w', encoding='utf-8').write(json.dumps(doc, indent=2))\n"
        f"json.dump({{'path': path, 'mode': os.stat(path).st_mode & 0o777}}, open({str(record)!r}, 'w'))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    return record


def _edit(db_file: Path, batch_id: str, *extra: str) -> int:
    return cli.main(["--db", str(db_file), "import", "edit", batch_id, *extra])


def test_an_editor_round_trip_withdraws_a_removed_row_and_amends_a_changed_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    record = _editor(
        tmp_path, monkeypatch, "doc['candidates'][1]['candidate']['role'] = 'CTO'\ndel doc['candidates'][2]"
    )

    assert _edit(db_file, batch_id) == 0

    rows = _rows(db_file)
    assert [row["status"] for row in rows] == ["pending", "pending", "rejected"]
    assert rows[1]["candidate"]["role"] == "CTO"
    out = capsys.readouterr().out
    assert "Applied 1 amendments and 1 withdrawals." in out and "Summary:" in out
    assert _no_terminal == ["Commit 2 pending candidates? [y/N] "]
    written = json.loads(record.read_text(encoding="utf-8"))
    assert written["mode"] == 0o600
    assert not Path(written["path"]).exists()
    assert not Path(written["path"]).parent.exists()


def test_confirming_the_prompt_commits_the_pending_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    _editor(tmp_path, monkeypatch, "del doc['candidates'][2]")
    monkeypatch.setattr(cli_imports, "_confirm_on_terminal", lambda prompt: True)

    assert _edit(db_file, batch_id) == 0

    assert [row["status"] for row in _rows(db_file)] == ["committed", "committed", "rejected"]
    assert "Committed 2 candidates" in capsys.readouterr().out


def test_a_batch_changed_between_apply_and_prompt_refuses_the_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    target = _rows(db_file)[2]["id"]
    _editor(tmp_path, monkeypatch, "pass")

    def amend_elsewhere(prompt: str) -> bool:
        assert cli.main(
            ["--db", str(db_file), "import", "amend", batch_id, target, "--patch", '{"value": "coffee"}', "--json"]
        ) == 0
        return True

    monkeypatch.setattr(cli_imports, "_confirm_on_terminal", amend_elsewhere)

    assert _edit(db_file, batch_id) == 1

    assert "batch_changed" in capsys.readouterr().err
    assert {row["status"] for row in _rows(db_file)} == {"pending"}


def test_no_commit_applies_without_prompting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    _editor(tmp_path, monkeypatch, "del doc['candidates'][2]")

    assert _edit(db_file, batch_id, "--no-commit") == 0

    assert _no_terminal == []
    assert f"Commit with: pctx import commit {batch_id} --all" in capsys.readouterr().out


def test_an_invalid_edit_refuses_naming_the_row_and_field_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    before = _rows(db_file)
    _editor(
        tmp_path,
        monkeypatch,
        "doc['candidates'][2]['candidate']['value'] = 'coffee'\ndoc['candidates'][1]['candidate']['role'] = None",
    )

    assert _edit(db_file, batch_id) == 1

    err = capsys.readouterr().err
    assert "import edit failed" in err
    assert "candidates[1] (#2)" in err and "role" in err
    assert _rows(db_file) == before
    assert _no_terminal == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("doc['candidates'][0]['status'] = 'rejected'", "review_field_changed"),
        ("doc['candidates'][0]['ordinal'] = 7", "review_field_changed"),
        ("doc['batch_id'] = 'another-batch'", "review_field_changed"),
        ("doc['candidates'].append(dict(doc['candidates'][0], id='new'))", "candidate_not_in_batch"),
    ],
)
def test_a_structural_edit_refuses_the_whole_apply_before_any_prompt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    _no_terminal: list[str],
    body: str,
    code: str,
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    before = _rows(db_file)
    _editor(tmp_path, monkeypatch, f"{body}\ndel doc['candidates'][2]")

    assert _edit(db_file, batch_id) == 1

    assert code in capsys.readouterr().err
    assert _rows(db_file) == before
    assert _no_terminal == []


def test_an_editor_exiting_nonzero_applies_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    before = _rows(db_file)
    record = _editor(tmp_path, monkeypatch, "del doc['candidates'][2]", exit_code=3)

    assert _edit(db_file, batch_id) == 1

    assert "editor exited with status 3" in capsys.readouterr().err
    assert _rows(db_file) == before
    assert _no_terminal == []
    assert not Path(json.loads(record.read_text(encoding="utf-8"))["path"]).exists()


def test_no_editor_configured_exits_2_naming_both_variables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)

    assert _edit(db_file, batch_id) == 2

    err = capsys.readouterr().err
    assert "VISUAL" in err and "EDITOR" in err


def test_visual_is_preferred_over_editor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    _editor(tmp_path, monkeypatch, "del doc['candidates'][2]")
    monkeypatch.setenv("VISUAL", os.environ["EDITOR"])
    monkeypatch.setenv("EDITOR", "definitely-not-an-editor-binary")

    assert _edit(db_file, batch_id, "--no-commit") == 0


class _Stdin:
    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)


def test_from_stdin_applies_without_prompting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    del doc["candidates"][2]
    monkeypatch.setattr(sys, "stdin", _Stdin(json.dumps(doc).encode("utf-8")))

    assert _edit(db_file, batch_id, "--from", "-") == 0

    assert [row["status"] for row in _rows(db_file)] == ["pending", "pending", "rejected"]
    assert _no_terminal == []


def test_from_with_no_commit_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--db", str(tmp_path / "people.db"), "import", "edit", "b", "--from", "-", "--no-commit"])
    assert raised.value.code == 2


def test_from_a_file_rendered_before_another_amendment_refuses_as_batch_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    target = doc["candidates"][2]["id"]
    assert cli.main(
        ["--db", str(db_file), "import", "amend", batch_id, target, "--patch", '{"value": "coffee"}', "--json"]
    ) == 0
    capsys.readouterr()
    doc["candidates"][1]["candidate"]["role"] = "CTO"
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert _edit(db_file, batch_id, "--from", str(path)) == 1

    assert "batch_changed" in capsys.readouterr().err
    rows = _rows(db_file)
    assert rows[2]["candidate"]["value"] == "coffee"
    assert rows[1]["candidate"]["role"] == "Engineer"


def _apply_unchanged(db_file: Path, batch_id: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    path = tmp_path / "unchanged.json"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    before = _rows(db_file)

    assert _edit(db_file, batch_id, "--from", str(path)) == 0

    assert "Applied 0 amendments and 0 withdrawals." in capsys.readouterr().out
    assert _rows(db_file) == before


def test_an_unchanged_document_over_1_mib_applies_back(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    conn = open_db(db_file)
    try:
        # Grow the batch past the stage-candidates request bound the way a restored batch could.
        row = conn.execute("SELECT * FROM import_staging ORDER BY created_at, id LIMIT 1").fetchone()
        for n in range(700):
            candidate = {"type": "fact", "person_candidate_id": row[0], "predicate": "note", "value": "x" * 1600}
            conn.execute(
                "INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at) "
                "VALUES (?, ?, 'review', ?, 'pending', ?)",
                (f"restored-{n:04d}", batch_id, json.dumps(candidate), "9999-01-01T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    _apply_unchanged(db_file, batch_id, tmp_path, capsys)


def test_an_unchanged_document_with_long_restored_ids_applies_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    conn = open_db(db_file)
    try:
        conn.execute("UPDATE import_staging SET id = ? WHERE candidate_json LIKE '%\"fact\"%'", ("r" * 20_000,))
        conn.commit()
    finally:
        conn.close()

    _apply_unchanged(db_file, batch_id, tmp_path, capsys)


def test_an_unchanged_document_with_long_match_candidate_names_applies_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    runtime = build_runtime(db_file)
    try:
        repository = SqlitePeopleRepository(runtime.conn)
        for n in range(12):
            # Two people sharing the candidate's name make it ambiguous; the long tail of the stored
            # name is what `match_candidates` has to cut and the read bound has to admit.
            repository.save_person(
                Person(id=f"person-{n:02d}-" + "i" * 300, canonical_name="Nadia Okonkwo", aliases=[],
                       created_at=_NOW, updated_at=_NOW)
            )
        runtime.conn.commit()
        runtime.conn.execute("UPDATE persons SET canonical_name = canonical_name || ?", (" " + "y" * 5000,))
        runtime.conn.commit()
    finally:
        runtime.close()
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    assert doc["candidates"][0]["match_candidates"], "the person row must be ambiguous for this test"

    _apply_unchanged(db_file, batch_id, tmp_path, capsys)


def test_a_row_amended_while_the_editor_is_open_refuses_as_batch_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, _no_terminal: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    target = _rows(db_file)[2]["id"]
    patch = json.dumps({"value": "coffee"})
    other_client = [
        sys.executable, "-c", "from people_context import cli; raise SystemExit(cli.main(__import__('sys').argv[1:]))",
        "--db", str(db_file), "import", "amend", batch_id, target, "--patch", patch, "--json",
    ]
    _editor(
        tmp_path,
        monkeypatch,
        f"import subprocess\nsubprocess.run({other_client!r}, check=True, capture_output=True)\n"
        "doc['candidates'][1]['candidate']['role'] = 'CTO'",
    )

    assert _edit(db_file, batch_id) == 1

    assert "batch_changed" in capsys.readouterr().err
    rows = _rows(db_file)
    assert rows[2]["candidate"]["value"] == "coffee"
    assert rows[1]["candidate"]["role"] == "Engineer"
    assert _no_terminal == []


def test_a_document_past_the_read_bound_is_refused_before_it_is_parsed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    path = tmp_path / "grown.json"
    path.write_text(json.dumps(doc, indent=2) + " " * 64, encoding="utf-8")
    monkeypatch.setattr(
        cli_imports, "edited_document_read_bound", lambda *args, **kwargs: len(json.dumps(doc, indent=2))
    )

    def never_parse(*args: object) -> None:
        raise AssertionError("an oversized document must not be diffed")

    monkeypatch.setattr(cli_imports, "review_document_edits", never_parse)

    assert _edit(db_file, batch_id, "--from", str(path)) == 1

    assert "grew past what this batch can hold" in capsys.readouterr().err


def test_from_a_file_whose_match_candidates_changed_since_rendering_still_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read-time projections follow the person table, which no batch edit owns."""
    db_file = tmp_path / "people.db"
    runtime = build_runtime(db_file)
    try:
        repository = SqlitePeopleRepository(runtime.conn)
        for n in range(2):
            repository.save_person(
                Person(id=f"person-{n}", canonical_name="Nadia Okonkwo", aliases=[], created_at=_NOW, updated_at=_NOW)
            )
        runtime.conn.commit()
    finally:
        runtime.close()
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    assert doc["candidates"][0]["match_candidates"]
    doc["candidates"][0]["match_candidates"] = [
        dict(entry, canonical_name="Renamed since") for entry in doc["candidates"][0]["match_candidates"]
    ]
    del doc["candidates"][2]
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    assert _edit(db_file, batch_id, "--from", str(path)) == 0

    assert [row["status"] for row in _rows(db_file)] == ["pending", "pending", "rejected"]


def test_an_editor_changing_match_candidates_still_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    _editor(tmp_path, monkeypatch, "doc['candidates'][0]['match_candidates'] = []")

    assert _edit(db_file, batch_id, "--no-commit") == 1

    assert "review_field_changed" in capsys.readouterr().err


def test_a_malformed_row_ordinal_is_never_echoed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    doc = _review(db_file, batch_id, capsys)
    doc["candidates"].append(dict(doc["candidates"][1], id=None, ordinal="private note"))
    monkeypatch.setattr(sys, "stdin", _Stdin(json.dumps(doc).encode("utf-8")))

    assert _edit(db_file, batch_id, "--from", "-") == 1

    err = capsys.readouterr().err
    assert "candidate_not_in_batch" in err
    assert "private note" not in err


def test_the_terminal_prompt_uses_the_console_devices_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_imports.os, "name", "nt")
    assert cli_imports._terminal_paths() == ("CONIN$", "CONOUT$")
    monkeypatch.setattr(cli_imports.os, "name", "posix")
    assert cli_imports._terminal_paths() == ("/dev/tty", "/dev/tty")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"C:\Windows\notepad.exe", [r"C:\Windows\notepad.exe"]),
        (r'"C:\Program Files\Editor\editor.exe" --wait', [r"C:\Program Files\Editor\editor.exe", "--wait"]),
    ],
)
def test_a_windows_editor_path_keeps_its_backslashes(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: list[str]
) -> None:
    monkeypatch.setattr(cli_imports.os, "name", "nt")
    monkeypatch.setenv("EDITOR", value)
    assert cli_imports._configured_editor() == expected


def test_a_posix_editor_command_is_split_like_a_shell_word_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "'my editor' --wait")
    assert cli_imports._configured_editor() == ["my editor", "--wait"]


def test_the_read_bound_is_measured_from_the_rendered_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headroom and the document must describe one snapshot of the batch."""
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    conn = open_db(db_file)
    try:
        stored = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(candidate_json AS BLOB)) + LENGTH(CAST(source AS BLOB))), 0) "
            "FROM import_staging WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    doc = _review(db_file, batch_id, capsys)
    seen: list[int] = []
    real_review = cli_imports._bounded_review

    def review_and_measure(runtime: Any, batch: str) -> Any:
        rendered = real_review(runtime, batch)
        seen.append(cli_imports.review_payload_bytes(rendered.candidates))
        return rendered

    monkeypatch.setattr(cli_imports, "_bounded_review", review_and_measure)
    captured: list[int] = []
    real_bound = cli_imports.edited_document_read_bound

    def spy(rendered_bytes: int, payload_bytes: int, *args: Any, **kwargs: Any) -> int:
        captured.append(payload_bytes)
        return real_bound(rendered_bytes, payload_bytes, *args, **kwargs)

    monkeypatch.setattr(cli_imports, "edited_document_read_bound", spy)
    monkeypatch.setattr(sys, "stdin", _Stdin(json.dumps(doc).encode("utf-8")))

    assert _edit(db_file, batch_id, "--from", "-") == 0

    assert captured == seen == [stored]


@pytest.mark.parametrize("from_stdin", [False, True])
def test_an_unchanged_document_with_crlf_line_endings_fits_the_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, from_stdin: bool
) -> None:
    """A batch at the ceiling leaves no headroom, so the bound is exactly the LF-rendered document."""
    db_file = tmp_path / "people.db"
    batch_id = _stage(db_file, tmp_path, capsys)
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    rendered = capsys.readouterr().out
    monkeypatch.setattr(cli_imports, "edited_document_read_bound", lambda *args, **kwargs: len(rendered.encode()))
    crlf = rendered.replace("\n", "\r\n").encode("utf-8")
    if from_stdin:
        monkeypatch.setattr(sys, "stdin", _Stdin(crlf))
        source = "-"
    else:
        path = tmp_path / "crlf.json"
        path.write_bytes(crlf)
        source = str(path)

    assert _edit(db_file, batch_id, "--from", source) == 0

    assert "Applied 0 amendments and 0 withdrawals." in capsys.readouterr().out
