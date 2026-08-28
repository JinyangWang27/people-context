"""Import duplicate reporting, `--force`, the redacted-claim refusal, and per-command guidance."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import open_db
from people_context.app.imports import SOURCE_PREVIOUSLY_REDACTED, source_previously_redacted_error
from people_context.cli.parser import build_parser

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


# -- guidance that names only a route the invoked command accepts -------


_AGENT_DIGEST = "a" * 64


def _candidate_file(tmp_path: Path, name: str = "candidates.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps([{"type": "person", "ref": "sofia", "name": "Sofia Rossi", "aliases": []}]),
        encoding="utf-8",
    )
    return path


def _stage_candidates(db_file: Path, input_path: Path, *extra: str) -> int:
    return cli.main(
        [
            "--db",
            str(db_file),
            "import",
            "stage-candidates",
            "--source",
            "2026-08-27 planning sync",
            "--input",
            str(input_path),
            "--source-kind",
            "meeting_transcript",
            "--content-digest",
            _AGENT_DIGEST,
            *extra,
        ]
    )


def test_the_redacted_refusal_itself_names_no_entry_point_specific_flag() -> None:
    """The rule is shared by three boundaries; only the route past it is per-boundary."""
    error = source_previously_redacted_error("01JSESSION")

    assert "--force" not in str(error)
    assert "--content-digest" not in str(error)


def test_stage_candidates_does_not_define_the_flag_stage_owns() -> None:
    """Pins the premise of the guidance below: this really is not a flag this command accepts."""
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["import", "stage-candidates", "--source", "s", "--input", "-", "--force"],
        )


def test_a_duplicate_agent_batch_is_not_pointed_at_a_flag_it_cannot_use(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`stage-candidates` shares the duplicate report, never `import stage`'s escape hatch.

    Its claim exists only because the caller computed a digest, so withholding that digest is the
    intentional-repeat route here. Naming `--force` would point at a workflow this command has no
    way to run.
    """
    db_file = tmp_path / "people.db"
    candidates = _candidate_file(tmp_path)
    assert _stage_candidates(db_file, candidates) == 0
    capsys.readouterr()

    assert _stage_candidates(db_file, candidates) == 0

    output = capsys.readouterr().out
    assert "already imported" in output
    assert "--force" not in output
    assert "--content-digest" in output
    assert len(_rows(db_file, "import_source_sessions")) == 1


def test_omitting_the_digest_is_the_agent_route_the_duplicate_report_offers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The offered route must actually work, not merely avoid naming the wrong flag."""
    db_file = tmp_path / "people.db"
    candidates = _candidate_file(tmp_path)
    assert _stage_candidates(db_file, candidates) == 0
    capsys.readouterr()

    code = cli.main(
        [
            "--db",
            str(db_file),
            "import",
            "stage-candidates",
            "--source",
            "2026-08-27 planning sync",
            "--input",
            str(candidates),
            "--source-kind",
            "meeting_transcript",
            "--json",
        ]
    )

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["duplicate"] is False
    # The claim-bearing receipt is untouched; the claimless one asserts nothing of its own.
    claims = sorted(row["claim_key"] is None for row in _rows(db_file, "import_source_sessions"))
    assert claims == [False, True]


def test_a_redacted_agent_claim_is_refused_with_its_own_route(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    candidates = _candidate_file(tmp_path)
    assert _stage_candidates(db_file, candidates, "--json") == 0
    batch = json.loads(capsys.readouterr().out)
    _commit_all(db_file, batch["batch_id"], capsys)
    _forget_everyone(db_file, capsys)
    assert [row["status"] for row in _rows(db_file, "import_source_sessions")] == ["redacted"]

    code = _stage_candidates(db_file, candidates)

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert SOURCE_PREVIOUSLY_REDACTED in captured.err
    assert "--content-digest" in captured.err
    assert "--force" not in captured.err
    assert "Traceback" not in captured.err
    assert _rows(db_file, "import_staging") == []


def test_a_fully_forgotten_forced_session_is_deleted_rather_than_retained(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Retention is for the claim, and a forced session competes for none.

    Keeping one would leave the digest of an erased artifact in the database — and in every later
    bundle — while suppressing nothing, because duplicate detection would never look it up again.
    """
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    default = _stage(db_file, source, capsys)
    forced = _stage(db_file, source, capsys, "--force")
    _commit_all(db_file, default["batch_id"], capsys)
    _commit_all(db_file, forced["batch_id"], capsys)

    _forget_everyone(db_file, capsys)

    sessions = _rows(db_file, "import_source_sessions")
    assert [row["id"] for row in sessions] == [default["source_session_id"]]
    assert sessions[0]["status"] == "redacted"
    assert sessions[0]["claim_key"] is not None
    assert _rows(db_file, "import_candidate_mappings") == []


def test_inline_content_alongside_a_path_is_refused_rather_than_silently_dropped(
    tmp_path: Path,
) -> None:
    """The released contract is exactly one input, and source tracking must not have relaxed it.

    Treating such a request as a path import would stage the file, discard the caller's content
    without a word, and persist a claim for an artifact they may not have meant to import.
    """
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    runtime = build_runtime(db_file)

    with pytest.raises(ImportExtractionError) as excinfo:
        runtime.use_cases.import_content.execute(
            "linkedin",
            content=source.read_text(encoding="utf-8"),
            path=str(source),
        )

    assert excinfo.value.code == "invalid_source"
    assert _rows(db_file, "import_source_sessions") == []
    assert _rows(db_file, "import_staging") == []


def test_an_ordinary_committed_batch_is_not_reported_as_awaiting_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Committing does not delete a batch's rows, it marks them committed.

    Counting rows rather than pending rows called a finished batch reviewable and sent the caller
    to a review with nothing left to decide — the exact distinction the flag exists to draw.
    """
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)
    _commit_all(db_file, first["batch_id"], capsys)
    assert _rows(db_file, "import_staging"), "this covers the case where cleanup has not run"

    document = _stage(db_file, source, capsys)

    assert document["duplicate"] is True
    assert document["reviewable"] is False
    # What the batch holds is still every row, which is the count worth reporting.
    assert document["candidate_count"] == len(_rows(db_file, "import_staging"))
    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)]) == 0
    output = capsys.readouterr().out
    assert "already committed" in output
    assert "pctx import review" not in output


def test_a_partly_committed_batch_still_points_at_what_is_left_to_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other side of the same rule: pending rows remain, so the batch is still reviewable."""
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    first = _stage(db_file, source, capsys)
    staged = _rows(db_file, "import_staging")
    assert len(staged) > 1
    commit = ["--db", str(db_file), "import", "commit", first["batch_id"], "--accept", staged[0]["id"]]
    assert cli.main(commit) == 0
    capsys.readouterr()

    document = _stage(db_file, source, capsys)

    assert document["duplicate"] is True
    assert document["reviewable"] is True
    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)]) == 0
    assert f"pctx import review {first['batch_id']}" in capsys.readouterr().out
