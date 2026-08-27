"""Source receipts are an addition to provenance, not a change to it."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import open_db

_MBOX = (
    "From alice@example.com Mon Jul 20 09:00:00 2026\n"
    "From: Alice Ahmed <alice@example.com>\n"
    "To: You <you@example.com>\n"
    "Subject: Weekly sync\n"
    "Message-ID: <one@example.com>\n"
    "Date: Mon, 20 Jul 2026 09:00:00 +0000\n"
    "\n"
    "body\n"
)

_ICS = "\r\n".join(
    [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "BEGIN:VEVENT",
        "UID:event-42@example.com",
        "DTSTART:20260720T090000Z",
        "DTEND:20260720T100000Z",
        "SUMMARY:Weekly sync",
        "ATTENDEE;CN=Alice Ahmed:mailto:alice@example.com",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
)


def _rows(db_file: Path, sql: str) -> list[sqlite3.Row]:
    conn = open_db(db_file)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _import_and_commit(db_file: Path, source_type: str, source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--db", str(db_file), "import", "stage", source_type, str(source)]) == 0
    output = capsys.readouterr().out
    batch_id = output.split("Staged batch ", 1)[1].split(" ", 1)[0]
    assert cli.main(["--db", str(db_file), "import", "commit", batch_id, "--all"]) == 0
    capsys.readouterr()


def test_message_derived_provenance_sessions_keep_their_meaning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = tmp_path / "mail.mbox"
    source.write_text(_MBOX, encoding="utf-8")

    _import_and_commit(db_file, "mbox", source, capsys)

    interactions = _rows(db_file, "SELECT provenance_source, provenance_session FROM interactions")
    assert [row["provenance_session"] for row in interactions] == ["<one@example.com>"]
    assert [row["provenance_source"] for row in interactions] == ["import/mbox"]


def test_event_derived_provenance_sessions_keep_their_meaning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = tmp_path / "calendar.ics"
    source.write_text(_ICS, encoding="utf-8")

    _import_and_commit(db_file, "ics", source, capsys)

    sessions = {row["provenance_session"] for row in _rows(db_file, "SELECT provenance_session FROM interactions")}
    assert sessions == {"event-42@example.com"}


def test_every_committed_candidate_traces_to_its_source_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    source = tmp_path / "mail.mbox"
    source.write_text(_MBOX, encoding="utf-8")

    _import_and_commit(db_file, "mbox", source, capsys)

    session = _rows(db_file, "SELECT id, source_kind FROM import_source_sessions")[0]
    mappings = _rows(db_file, "SELECT * FROM import_candidate_mappings")
    assert mappings
    assert {row["source_session_id"] for row in mappings} == {session["id"]}
    assert session["source_kind"] == "mbox"
    # Every live mapping resolves to a row that actually exists.
    people = {row["id"] for row in _rows(db_file, "SELECT id FROM persons")}
    interactions = {row["id"] for row in _rows(db_file, "SELECT id FROM interactions")}
    for row in mappings:
        assert row["entity_id"] in (people | interactions)


def test_a_receipt_stores_no_path_or_source_body(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    source = tmp_path / "mail.mbox"
    source.write_text(_MBOX, encoding="utf-8")

    _import_and_commit(db_file, "mbox", source, capsys)

    stored = " ".join(
        str(value)
        for row in _rows(db_file, "SELECT * FROM import_source_sessions")
        for value in tuple(row)
    )
    assert str(source) not in stored
    assert "mail.mbox" not in stored
    assert "Weekly sync" not in stored
    assert "body" not in stored


def test_an_inline_content_import_stays_untracked(tmp_path: Path) -> None:
    """There is no source artifact to identify, so nothing pretends there is one."""
    from people_context.adapters.runtime import build_runtime

    runtime = build_runtime(tmp_path / "people.db")
    try:
        batch = runtime.use_cases.import_content.execute("email", content=_MBOX.split("\n", 1)[1])
    finally:
        runtime.close()

    assert batch.source_session_id is None
    assert batch.duplicate is False
    assert _rows(tmp_path / "people.db", "SELECT * FROM import_source_sessions") == []


def test_the_clock_used_for_receipts_is_the_injected_one(tmp_path: Path) -> None:
    from people_context.adapters.runtime import build_runtime

    moment = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    class _Clock:
        def now(self) -> datetime:
            return moment

    source = tmp_path / "chat.txt"
    source.write_text("[2026-07-20, 09:00:00] Priya Nair: hi\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "people.db", clock=_Clock())
    try:
        runtime.use_cases.import_content.execute("whatsapp", path=str(source), self_sender="You")
    finally:
        runtime.close()

    created = _rows(tmp_path / "people.db", "SELECT created_at FROM import_source_sessions")
    assert [row["created_at"] for row in created] == [moment.isoformat()]
