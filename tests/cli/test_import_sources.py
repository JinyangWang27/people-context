"""`pctx import stage` duplicate reporting, `--force`, and the redacted-claim refusal."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db
from people_context.app.imports import SOURCE_PREVIOUSLY_REDACTED

_LINKEDIN_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_URL = "https://example.invalid/in/sr"

_CHAT = (
    "[2026-07-20, 09:00:00] You: morning\n"
    "[2026-07-20, 09:01:00] Priya Nair: morning!\n"
)


def _linkedin(tmp_path: Path, name: str = "connections.csv") -> Path:
    source = tmp_path / name
    source.write_text(
        "\n".join(
            [
                _LINKEDIN_HEADERS,
                f"Sofia,Rossi,{_URL},sofia@example.com,Globex,Designer,23 Jul 2026,note",
            ]
        ),
        encoding="utf-8",
    )
    return source


def _stage(
    db_file: Path,
    source: Path,
    capsys: pytest.CaptureFixture[str],
    *extra: str,
    source_type: str = "linkedin",
) -> dict[str, Any]:
    code = cli.main(["--db", str(db_file), "import", "stage", source_type, str(source), *extra, "--json"])
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def _rows(db_file: Path, table: str) -> list[sqlite3.Row]:
    conn = open_db(db_file)
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - fixed test constants
    finally:
        conn.close()


def _commit_all(db_file: Path, batch_id: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all"]) == 0
    capsys.readouterr()


def test_staging_reports_the_source_session_it_created(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    document = _stage(db_file, _linkedin(tmp_path), capsys)

    assert document["source_session_id"]
    assert document["duplicate"] is False
    sessions = _rows(db_file, "import_source_sessions")
    assert len(sessions) == 1
    assert sessions[0]["source_kind"] == "linkedin"
    assert sessions[0]["batch_id"] == document["batch_id"]
    assert len(sessions[0]["content_digest"]) == 64


def test_restaging_the_same_export_reports_the_existing_batch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path), capsys)

    second = _stage(db_file, _linkedin(tmp_path), capsys)

    assert second["duplicate"] is True
    assert second["batch_id"] == first["batch_id"]
    assert second["source_session_id"] == first["source_session_id"]
    assert len(_rows(db_file, "import_source_sessions")) == 1
    assert len(_rows(db_file, "import_staging")) == first["candidate_count"]


def test_the_human_summary_says_the_source_was_already_imported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    _stage(db_file, _linkedin(tmp_path), capsys)

    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert "already imported" in output
    assert "--force" in output


def test_a_copy_of_the_same_bytes_at_another_path_is_still_a_duplicate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The claim is over content, not over where the file happens to live."""
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path), capsys)

    second = _stage(db_file, _linkedin(tmp_path, "copy.csv"), capsys)

    assert second["duplicate"] is True
    assert second["batch_id"] == first["batch_id"]


def test_a_changed_export_is_a_new_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)
    source.write_text(
        source.read_text(encoding="utf-8") + f"\nAmina,Haddad,{_URL},amina@example.com,Acme,Engineer,22 Jul 2026,n",
        encoding="utf-8",
    )

    second = _stage(db_file, source, capsys)

    assert second["duplicate"] is False
    assert second["batch_id"] != first["batch_id"]
    assert len(_rows(db_file, "import_source_sessions")) == 2


def test_force_creates_a_distinct_processing_session_for_one_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path), capsys)

    forced = _stage(db_file, _linkedin(tmp_path), capsys, "--force")

    assert forced["duplicate"] is False
    assert forced["batch_id"] != first["batch_id"]
    assert forced["source_session_id"] != first["source_session_id"]
    sessions = {row["id"]: row for row in _rows(db_file, "import_source_sessions")}
    assert len(sessions) == 2
    digests = {row["content_digest"] for row in sessions.values()}
    assert len(digests) == 1, "a forced session keeps the same digest"
    # Only the default session owns the canonical claim; the forced one asserts none.
    assert sorted(row["claim_key"] is None for row in sessions.values()) == [False, True]


def test_force_does_not_weaken_the_default_rule_for_later_invocations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    first = _stage(db_file, _linkedin(tmp_path), capsys)
    _stage(db_file, _linkedin(tmp_path), capsys, "--force")

    plain = _stage(db_file, _linkedin(tmp_path), capsys)

    assert plain["duplicate"] is True
    assert plain["batch_id"] == first["batch_id"]


def test_a_changed_chat_self_sender_is_a_distinct_claim(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same bytes parsed under different self identity are a different extraction."""
    db_file = tmp_path / "people.db"
    chat = tmp_path / "chat.txt"
    chat.write_text(_CHAT, encoding="utf-8")
    first = _stage(db_file, chat, capsys, "--self-sender", "You", source_type="whatsapp")

    second = _stage(db_file, chat, capsys, "--self-sender", "Priya Nair", source_type="whatsapp")

    assert second["duplicate"] is False
    assert second["batch_id"] != first["batch_id"]
    fingerprints = {row["extraction_fingerprint"] for row in _rows(db_file, "import_source_sessions")}
    assert len(fingerprints) == 2


def test_the_same_chat_self_sender_shares_one_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    chat = tmp_path / "chat.txt"
    chat.write_text(_CHAT, encoding="utf-8")
    first = _stage(db_file, chat, capsys, "--self-sender", "You", source_type="whatsapp")

    second = _stage(db_file, chat, capsys, "--self-sender", "You", source_type="whatsapp")

    assert second["duplicate"] is True
    assert second["batch_id"] == first["batch_id"]


def test_an_optional_label_is_stored_on_the_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    _stage(db_file, _linkedin(tmp_path), capsys, "--label", "Work connections", "--external-source-id", "EXP-1")

    session = _rows(db_file, "import_source_sessions")[0]
    assert session["label"] == "Work connections"
    assert session["external_source_id"] == "EXP-1"


def test_a_refused_label_stages_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    code = cli.main(
        ["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path)), "--label", "x" * 300]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _rows(db_file, "import_source_sessions") == []
    assert _rows(db_file, "import_staging") == []


def test_a_duplicate_of_a_committed_batch_reports_what_it_still_holds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completed batch may have lost its staging rows — to cleanup, or to a v2 restore.

    Its durable mappings are what remain, so the report counts those and stops recommending a
    review of a batch that review can no longer find.
    """
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)
    _commit_all(db_file, first["batch_id"], capsys)
    conn = open_db(db_file)
    try:
        conn.execute("DELETE FROM import_staging WHERE batch_id = ?", (first["batch_id"],))
        conn.commit()
    finally:
        conn.close()

    document = _stage(db_file, source, capsys)

    assert document["duplicate"] is True
    assert document["batch_id"] == first["batch_id"]
    assert document["reviewable"] is False
    assert document["candidate_count"] == len(_rows(db_file, "import_candidate_mappings"))
    assert document["candidate_count"] > 0


def test_the_summary_for_a_committed_duplicate_does_not_point_at_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)
    _commit_all(db_file, first["batch_id"], capsys)
    conn = open_db(db_file)
    try:
        conn.execute("DELETE FROM import_staging WHERE batch_id = ?", (first["batch_id"],))
        conn.commit()
    finally:
        conn.close()

    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)]) == 0

    output = capsys.readouterr().out
    assert "already imported" in output
    assert "already committed" in output
    assert "pctx import review" not in output
    assert "--force" in output


def test_a_still_reviewable_duplicate_keeps_pointing_at_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)

    document = _stage(db_file, source, capsys)

    assert document["reviewable"] is True
    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)]) == 0
    output = capsys.readouterr().out
    assert f"pctx import review {first['batch_id']}" in output


# -- the redacted-claim refusal contract -------------------------------


def _forget_everyone(db_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conn = open_db(db_file)
    try:
        person_ids = [row["id"] for row in conn.execute("SELECT id FROM persons")]
    finally:
        conn.close()
    for person_id in person_ids:
        assert cli.main(["--db", str(db_file), "delete", person_id, "--yes"]) == 0
    capsys.readouterr()


@pytest.fixture
def redacted_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, Path]:
    """A database whose only import source was committed and then fully forgotten."""
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    batch = _stage(db_file, source, capsys)
    _commit_all(db_file, batch["batch_id"], capsys)
    _forget_everyone(db_file, capsys)
    sessions = _rows(db_file, "import_source_sessions")
    assert [row["status"] for row in sessions] == ["redacted"]
    return db_file, source


def test_a_redacted_claim_refuses_without_fabricating_a_batch(
    redacted_source: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file, source = redacted_source
    before = _rows(db_file, "import_source_sessions")

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert SOURCE_PREVIOUSLY_REDACTED in captured.err
    assert "--force" in captured.err
    assert "Traceback" not in captured.err
    assert _rows(db_file, "import_staging") == []
    assert [dict(row) for row in _rows(db_file, "import_source_sessions")] == [dict(row) for row in before]


def test_a_redacted_claim_writes_no_document_even_under_json(
    redacted_source: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file, source = redacted_source

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source), "--json"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert SOURCE_PREVIOUSLY_REDACTED in captured.err


def test_a_redacted_refusal_names_no_former_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    batch = _stage(db_file, source, capsys, "--label", "Interview with Sofia")
    _commit_all(db_file, batch["batch_id"], capsys)
    _forget_everyone(db_file, capsys)

    cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)])

    captured = capsys.readouterr()
    assert "Interview with Sofia" not in captured.err
    assert batch["batch_id"] not in captured.err


def test_a_forced_retry_after_redaction_stages_an_ordinary_batch(
    redacted_source: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file, source = redacted_source

    document = _stage(db_file, source, capsys, "--force")

    assert document["duplicate"] is False
    assert document["format"] == "people-context-import-batch"
    assert document["version"] == 1
    assert document["batch_id"]
    assert document["source_session_id"]
    assert cli.main(["--db", str(db_file), "import", "review", document["batch_id"]]) == 0
    # The terminal receipt is still there, still non-restageable by default.
    statuses = sorted(row["status"] for row in _rows(db_file, "import_source_sessions"))
    assert statuses == ["redacted", "staged"]
