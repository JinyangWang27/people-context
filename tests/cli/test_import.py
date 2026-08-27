"""`pctx import` stage, review, and commit behaviour at the CLI process boundary."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.importers.router import SUPPORTED_IMPORT_SOURCES
from people_context.adapters.sqlite import open_db
from people_context.app.imports import (
    IMPORT_BATCH_FORMAT,
    IMPORT_BATCH_VERSION,
    IMPORT_COMMIT_FORMAT,
    IMPORT_COMMIT_VERSION,
    IMPORT_REVIEW_FORMAT,
    IMPORT_REVIEW_VERSION,
    ImportReviewRow,
)
from people_context.cli import imports as cli_imports
from people_context.cli.parser import build_parser
from people_context.cli.rendering import print_import_review

_LINKEDIN_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_URL_SENTINEL = "LINKEDIN-URL-MUST-NOT-LEAK-41d7"
_NOTE_SENTINEL = "LINKEDIN-NOTE-MUST-NOT-LEAK-92ac"
_WHATSAPP_BODY_SENTINEL = "WHATSAPP-BODY-MUST-NOT-LEAK-5b1e"


def _linkedin(tmp_path: Path) -> Path:
    source = tmp_path / "connections.csv"
    source.write_text(
        "\n".join(
            [
                _LINKEDIN_HEADERS,
                f"Amina,Haddad,{_URL_SENTINEL},amina@example.com,Acme,Engineer,22 Jul 2026,{_NOTE_SENTINEL}",
                f"Sofia,Rossi,{_URL_SENTINEL},sofia@example.com,Globex,Designer,23 Jul 2026,{_NOTE_SENTINEL}",
            ]
        ),
        encoding="utf-8",
    )
    return source


def _ics(tmp_path: Path) -> Path:
    source = tmp_path / "calendar.ics"
    source.write_text(
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:event-1\r\n"
        "DTSTART:20260722T090000Z\r\n"
        "SUMMARY:Planning\r\n"
        "ATTENDEE;CN=Amina Haddad:mailto:amina@example.com\r\n"
        "ATTENDEE;CN=Sofia Rossi:mailto:sofia@example.com\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n",
        encoding="utf-8",
    )
    return source


def _whatsapp(tmp_path: Path) -> Path:
    source = tmp_path / "chat.txt"
    source.write_text(
        f"[22/07/2026, 09:00:00] You: {_WHATSAPP_BODY_SENTINEL}\n"
        f"[22/07/2026, 09:01:00] Amina Haddad: {_WHATSAPP_BODY_SENTINEL}\n",
        encoding="utf-8",
    )
    return source


def _staged_batch(
    db_file: Path,
    source: Path,
    capsys: pytest.CaptureFixture[str],
    source_type: str = "linkedin",
    *extra: str,
) -> dict[str, object]:
    code = cli.main(["--db", str(db_file), "import", "stage", source_type, str(source), *extra, "--json"])
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


def test_the_parser_accepts_exactly_the_router_source_types() -> None:
    parser = build_parser()

    for source in SUPPORTED_IMPORT_SOURCES:
        assert parser.parse_args(["import", "stage", source, "file"]).source == source

    with pytest.raises(SystemExit) as refusal:
        parser.parse_args(["import", "stage", "signal", "file"])
    assert refusal.value.code == 2


def test_commit_requires_exactly_one_selector() -> None:
    parser = build_parser()

    assert parser.parse_args(["import", "commit", "batch", "--all"]).all is True
    assert parser.parse_args(["import", "commit", "batch", "--accept", "a", "--accept", "b"]).accept == ["a", "b"]

    for argv in (
        ["import", "commit", "batch"],
        ["import", "commit", "batch", "--all", "--accept", "a"],
    ):
        with pytest.raises(SystemExit) as refusal:
            parser.parse_args(argv)
        assert refusal.value.code == 2


def test_an_import_subcommand_is_required() -> None:
    with pytest.raises(SystemExit) as refusal:
        build_parser().parse_args(["import"])

    assert refusal.value.code == 2


def test_stage_reports_the_batch_and_the_next_command_without_committing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path))])

    assert code == 0
    output = capsys.readouterr().out
    assert "nothing is committed yet" in output
    assert "Review with: pctx import review " in output
    rows = _staging_rows(db_file)
    assert rows and all(row["status"] == "pending" for row in rows)
    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    finally:
        conn.close()


def test_stage_never_echoes_discarded_source_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path))])
    staged = capsys.readouterr()

    conn = open_db(db_file)
    try:
        persisted = " ".join(str(tuple(row)) for row in conn.execute("SELECT * FROM import_staging"))
    finally:
        conn.close()
    for sentinel in (_URL_SENTINEL, _NOTE_SENTINEL):
        assert sentinel not in staged.out
        assert sentinel not in staged.err
        assert sentinel not in persisted


def test_a_whatsapp_stage_takes_the_self_sender_and_keeps_bodies_out_of_everything(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    code = cli.main(
        [
            "--db",
            str(db_file),
            "import",
            "stage",
            "whatsapp",
            str(_whatsapp(tmp_path)),
            "--self-sender",
            "You",
        ]
    )

    assert code == 0
    captured = capsys.readouterr()
    conn = open_db(db_file)
    try:
        persisted = " ".join(str(tuple(row)) for row in conn.execute("SELECT * FROM import_staging"))
        names = [
            json.loads(row["candidate_json"])["name"]
            for row in conn.execute("SELECT candidate_json FROM import_staging")
            if json.loads(row["candidate_json"])["type"] == "person"
        ]
    finally:
        conn.close()
    assert _WHATSAPP_BODY_SENTINEL not in captured.out + captured.err + persisted
    assert names == ["Amina Haddad"]


def test_an_ics_stage_reaches_the_calendar_extractor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    document = _staged_batch(db_file, _ics(tmp_path), capsys, "ics")

    assert document["candidate_count"] == 3


def test_stage_reports_the_messages_and_cards_the_extractor_skipped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip counts are the operator's only signal that a source was not fully understood."""
    mailbox = tmp_path / "archive.mbox"
    mailbox.write_bytes(
        b"From sender@example.com Thu Jul 22 09:00:00 2026\n"
        b"From: Amina <amina@example.com>\n"
        b"Message-ID: <undated@example.com>\n"
        b"\n"
        b"body\n"
        b"\n"
        b"From sender@example.com Thu Jul 22 09:00:00 2026\n"
        b"From: Sofia <sofia@example.com>\n"
        b"\n"
        b"body\n"
    )

    assert cli.main(["--db", str(tmp_path / "people.db"), "import", "stage", "mbox", str(mailbox)]) == 0

    output = capsys.readouterr().out
    assert "Skipped undated messages with ids: <undated@example.com>" in output
    assert "Skipped undated messages without ids: 1" in output


def test_stage_reports_each_independently_skipped_card_by_its_position(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cards = tmp_path / "contacts.vcf"
    cards.write_text(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Amina Haddad\r\nEMAIL:amina@example.com\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Old Dialect\r\nEND:VCARD\r\n",
        encoding="utf-8",
    )

    assert cli.main(["--db", str(tmp_path / "people.db"), "import", "stage", "vcard", str(cards)]) == 0

    output = capsys.readouterr().out
    assert "Skipped card 2: unsupported_version" in output
    assert "Old Dialect" not in output


def test_a_source_that_disappears_after_the_readable_check_is_refused_safely(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path readable a moment ago is not a path the extractor is guaranteed to read."""
    db_file = tmp_path / "people.db"
    source = _linkedin(tmp_path)
    readable = cli_imports._readable_source

    def vanish_after_check(raw_path: str) -> Path | None:
        checked = readable(raw_path)
        assert checked is not None
        checked.unlink()
        return checked

    monkeypatch.setattr(cli_imports, "_readable_source", vanish_after_check)

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(source)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read source file" in captured.err
    assert _staging_rows(db_file) == []


def test_a_missing_source_path_is_refused_before_the_database_is_touched(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(tmp_path / "absent.csv")])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read source file" in captured.err
    assert _staging_rows(db_file) == []


def test_a_source_without_candidates_is_a_non_zero_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    empty = tmp_path / "empty.csv"
    empty.write_text(_LINKEDIN_HEADERS, encoding="utf-8")

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(empty)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no external import candidates" in captured.err
    assert _staging_rows(db_file) == []


def test_an_extraction_failure_is_non_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    malformed = tmp_path / "malformed.csv"
    malformed.write_text("Nickname,Handle\nx,y\n", encoding="utf-8")

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(malformed)])

    assert code == 1
    assert "import staging failed" in capsys.readouterr().err


def test_review_lists_every_candidate_with_its_id_status_and_type(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)

    code = cli.main(["--db", str(db_file), "import", "review", str(batch["batch_id"])])

    assert code == 0
    captured = capsys.readouterr()
    assert "Amina Haddad" in captured.out
    assert "  pending  person  " in captured.out
    assert "  pending  affiliation  " in captured.out
    assert f"pctx import commit {batch['batch_id']} --all" in captured.out
    assert "personal data" in captured.err


def test_review_tells_two_proposed_interactions_apart(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Selective `--accept` is unusable if every interaction row renders identically."""
    db_file = tmp_path / "people.db"
    chat = tmp_path / "chat.txt"
    chat.write_text(
        f"[22/07/2026, 09:00:00] Amina Haddad: {_WHATSAPP_BODY_SENTINEL}\n"
        f"[23/07/2026, 09:00:00] Sofia Rossi: {_WHATSAPP_BODY_SENTINEL}\n",
        encoding="utf-8",
    )
    batch = _staged_batch(db_file, chat, capsys, "whatsapp")

    assert cli.main(["--db", str(db_file), "import", "review", str(batch["batch_id"])]) == 0

    captured = capsys.readouterr()
    interactions = [line for line in captured.out.splitlines() if "  interaction  " in line]
    assert len(interactions) == 2
    assert len(set(interactions)) == 2
    assert "2026-07-22" in captured.out
    assert "2026-07-23" in captured.out
    assert "whatsapp" in captured.out
    assert "Amina Haddad" in captured.out
    assert _WHATSAPP_BODY_SENTINEL not in captured.out + captured.err


def test_review_caps_the_participants_it_names_for_one_interaction(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    attendees = "".join(
        f"ATTENDEE;CN=Person {index}:mailto:p{index}@example.com\r\n" for index in range(9)
    )
    event = tmp_path / "big.ics"
    event.write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nDTSTART:20260722T090000Z\r\n"
        f"SUMMARY:Planning\r\n{attendees}END:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    batch = _staged_batch(db_file, event, capsys, "ics")

    assert cli.main(["--db", str(db_file), "import", "review", str(batch["batch_id"])]) == 0

    interaction = next(
        line for line in capsys.readouterr().out.splitlines() if "  interaction  " in line
    )
    assert "+5 more" in interaction
    assert "Person 0" in interaction
    assert "Person 8" not in interaction


def test_review_renders_an_interaction_that_names_no_participants(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`participant_refs` has no minimum length, so an agent batch can stage an empty one."""
    print_import_review(
        [
            ImportReviewRow(
                id="interaction-1",
                source="import/agent:notes",
                status="pending",
                candidate={
                    "type": "interaction",
                    "summary": "Team retrospective",
                    "participant_candidate_ids": [],
                    "date": "2026-07-22T09:00:00Z",
                    "channel": "in-person",
                },
            )
        ]
    )

    assert (
        "interaction-1  pending  interaction  Team retrospective · 2026-07-22 · in-person"
        in capsys.readouterr().out
    )


def test_an_undecodable_source_is_a_concise_refusal_rather_than_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    card = tmp_path / "latin1.vcf"
    card.write_bytes("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Café Owner\r\nEND:VCARD\r\n".encode("latin-1"))

    code = cli.main(["--db", str(db_file), "import", "stage", "vcard", str(card)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not valid utf-8 text" in captured.err
    assert "Traceback" not in captured.err
    assert _staging_rows(db_file) == []


def test_a_card_with_an_unresolvable_charset_never_reaches_the_operator_as_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Property-level decoding fails after the file decoded, so it needs its own guard."""
    db_file = tmp_path / "people.db"
    cards = tmp_path / "mixed.vcf"
    cards.write_text(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN;ENCODING=QUOTED-PRINTABLE;CHARSET=x-invalid:Alice\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sofia Rossi\r\nEMAIL:sofia@example.com\r\nEND:VCARD\r\n",
        encoding="utf-8",
    )

    code = cli.main(["--db", str(db_file), "import", "stage", "vcard", str(cards)])

    assert code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Skipped card 1: malformed_card" in captured.out
    assert len(_staging_rows(db_file)) == 1


def test_a_vcard_file_of_only_undecodable_cards_refuses_concisely(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    cards = tmp_path / "all-bad.vcf"
    cards.write_text(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN;ENCODING=QUOTED-PRINTABLE;CHARSET=x-invalid:Alice\r\nEND:VCARD\r\n",
        encoding="utf-8",
    )

    code = cli.main(["--db", str(db_file), "import", "stage", "vcard", str(cards)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err
    assert "no external import candidates" in captured.err
    assert _staging_rows(db_file) == []


def test_review_shows_a_matched_existing_person(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path))]) == 0
    capsys.readouterr()
    first = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    assert cli.main(["--db", str(db_file), "import", "commit", str(first["batch_id"]), "--all"]) == 0
    capsys.readouterr()

    second = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    assert cli.main(["--db", str(db_file), "import", "review", str(second["batch_id"])]) == 0

    assert "matches existing person" in capsys.readouterr().out


def test_an_unknown_batch_is_refused_by_review_and_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    _staged_batch(db_file, _linkedin(tmp_path), capsys)

    assert cli.main(["--db", str(db_file), "import", "review", "no-such-batch"]) == 1
    assert "batch not found" in capsys.readouterr().err
    assert cli.main(["--db", str(db_file), "import", "commit", "no-such-batch", "--all"]) == 1
    assert "batch not found" in capsys.readouterr().err


def test_a_candidate_outside_the_batch_is_refused_without_committing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)

    code = cli.main(
        ["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--accept", "not-in-batch"]
    )

    assert code == 1
    assert "accepted candidate does not belong to batch" in capsys.readouterr().err
    assert all(row["status"] == "pending" for row in _staging_rows(db_file))


def test_partial_acceptance_commits_only_the_named_candidates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    review = _review_document(db_file, str(batch["batch_id"]), capsys)
    person = next(row for row in review["candidates"] if row["candidate"]["type"] == "person")

    code = cli.main(
        ["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--accept", person["id"], "--json"]
    )

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["committed_ids"] == [person["id"]]
    assert document["unresolved_ids"] == []
    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
    finally:
        conn.close()


def test_an_accepted_dependant_without_its_person_stays_unresolved(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    review = _review_document(db_file, str(batch["batch_id"]), capsys)
    affiliation = next(row for row in review["candidates"] if row["candidate"]["type"] == "affiliation")

    code = cli.main(
        ["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--accept", affiliation["id"], "--json"]
    )

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["committed_ids"] == []
    assert document["unresolved_ids"] == [affiliation["id"]]


def test_commit_all_is_explicit_approval_and_asks_nothing_further(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    monkeypatch.setattr("builtins.input", _refuse_prompt)

    code = cli.main(["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--all"])

    assert code == 0
    output = capsys.readouterr().out
    assert "Committed 6 candidates" in output
    assert all(row["status"] == "committed" for row in _staging_rows(db_file))


def test_a_second_commit_reports_already_committed_rows_rather_than_rewriting_them(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    assert cli.main(["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--all"]) == 0
    capsys.readouterr()

    code = cli.main(["--db", str(db_file), "import", "commit", str(batch["batch_id"]), "--all", "--json"])

    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["committed_ids"] == []
    assert len(document["skipped_ids"]) == 6
    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    finally:
        conn.close()


def test_repeated_accept_ids_are_deduplicated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)
    review = _review_document(db_file, str(batch["batch_id"]), capsys)
    person = next(row for row in review["candidates"] if row["candidate"]["type"] == "person")

    code = cli.main(
        [
            "--db",
            str(db_file),
            "import",
            "commit",
            str(batch["batch_id"]),
            "--accept",
            person["id"],
            "--accept",
            person["id"],
            "--json",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["committed_ids"] == [person["id"]]


def test_every_json_mode_writes_exactly_one_versioned_document_to_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(_linkedin(tmp_path)), "--json"]) == 0
    staged = capsys.readouterr()
    batch = json.loads(staged.out)
    assert staged.out.endswith("}\n")
    assert batch["format"] == IMPORT_BATCH_FORMAT
    assert batch["version"] == IMPORT_BATCH_VERSION
    assert batch["candidate_count"] == 6
    assert batch["skipped_message_ids"] == []
    assert batch["skipped_without_id"] == 0
    assert batch["skipped_cards"] == []

    assert cli.main(["--db", str(db_file), "import", "review", batch["batch_id"], "--json"]) == 0
    reviewed = capsys.readouterr()
    review = json.loads(reviewed.out)
    assert reviewed.err == ""
    assert review["format"] == IMPORT_REVIEW_FORMAT
    assert review["version"] == IMPORT_REVIEW_VERSION
    assert review["batch_id"] == batch["batch_id"]
    assert [row["status"] for row in review["candidates"]] == ["pending"] * 6
    assert {row["source"] for row in review["candidates"]} == {"import/linkedin"}

    assert cli.main(["--db", str(db_file), "import", "commit", batch["batch_id"], "--all", "--json"]) == 0
    committed = json.loads(capsys.readouterr().out)
    assert committed["format"] == IMPORT_COMMIT_FORMAT
    assert committed["version"] == IMPORT_COMMIT_VERSION
    assert committed["batch_id"] == batch["batch_id"]
    assert committed["unresolved_ids"] == []
    assert committed["skipped_ids"] == []


def test_the_review_document_carries_the_staged_candidate_unchanged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    batch = _staged_batch(db_file, _linkedin(tmp_path), capsys)

    review = _review_document(db_file, str(batch["batch_id"]), capsys)

    conn = open_db(db_file)
    try:
        stored = {
            row["id"]: json.loads(row["candidate_json"])
            for row in conn.execute("SELECT id, candidate_json FROM import_staging")
        }
    finally:
        conn.close()
    assert {row["id"]: row["candidate"] for row in review["candidates"]} == stored


def test_json_refusals_leave_stdout_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"

    assert cli.main(["--db", str(db_file), "import", "review", "missing", "--json"]) == 1
    refused = capsys.readouterr()

    assert refused.out == ""
    assert refused.err != ""


def _review_document(db_file: Path, batch_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    assert cli.main(["--db", str(db_file), "import", "review", batch_id, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _refuse_prompt(prompt: str = "") -> str:
    raise AssertionError(f"commit must not prompt: {prompt!r}")
