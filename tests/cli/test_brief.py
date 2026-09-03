"""CLI behaviour for the `pctx brief` person brief (M14.1)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.records import RecordFact, RecordFactInput, SetReminder, SetReminderInput
from people_context.cli import main
from people_context.domain.person import Person
from people_context.domain.reminder import ReminderKind
from people_context.domain.shared import Sensitivity

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
_DUE = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
# Matches `tests/adapters/filesystem/test_private_file.py`: the stale-destination fixture must
# differ from 0o600 so a mode-retaining `O_TRUNC` write is caught, but must grant nothing to
# group or other, because CodeQL `security-extended` reports `py/overly-permissive-file` for any
# non-owner bit even in a test.
_STALE_FIXTURE_MODE = 0o700


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _seed(db_path: Path, *, second_person: str | None = None) -> str:
    """Create one person with an ordinary fact, a sensitive fact, and two reminder kinds."""
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        audit = SqliteAuditLog(conn)
        person = Person(canonical_name="Alice Zhang", summary="A friend")
        repository.save_person(person)
        if second_person is not None:
            repository.save_person(Person(canonical_name=second_person))
        record_fact = RecordFact(repository, records, audit, _Clock())
        record_fact.execute(RecordFactInput(person_id=person.id, predicate="role", value="Engineer", source="cli"))
        record_fact.execute(
            RecordFactInput(
                person_id=person.id,
                predicate="health",
                value="Elevated detail",
                sensitivity=Sensitivity.SENSITIVE,
                source="cli",
            )
        )
        set_reminder = SetReminder(repository, records, audit, _Clock())
        set_reminder.execute(
            SetReminderInput(
                person_id=person.id, text="Send the notes", kind=ReminderKind.FOLLOW_UP, due_at=_DUE, source="cli"
            )
        )
        set_reminder.execute(
            SetReminderInput(
                person_id=person.id,
                text="Prefers short messages",
                kind=ReminderKind.COMMUNICATION_NOTE,
                source="cli",
            )
        )
        return person.id
    finally:
        conn.close()


def test_markdown_brief_goes_to_stdout_and_stays_ordinary_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    person_id = _seed(db_path)

    assert main(["--db", str(db_path), "brief", "Alice Zhang"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("# Alice Zhang\n")
    assert f"- **Person id:** {person_id}" in out
    assert "- **Context disclosure:** ordinary" in out
    assert "- **Guidance disclosure:** ordinary (never widened)" in out
    assert "outside the server's disclosure controls" in out
    assert "role: Engineer" in out
    assert "Elevated detail" not in out
    # Both reminder kinds appear, which only `ListReminders` supplies.
    assert "follow_up (due 2026-06-01T12:30:00+00:00): Send the notes" in out
    assert "communication_note (no due date): Prefers short messages" in out


def test_include_sensitive_widens_context_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)

    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--include-sensitive", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["disclosure"] == {
        "include_sensitive": True,
        "context": "sensitive",
        "guidance": "ordinary",
        "notice": payload["disclosure"]["notice"],
    }
    assert {fact["value"] for fact in payload["facts"]} == {"Engineer", "Elevated detail"}
    assert payload["guidance"]["disclosure"] == "ordinary"


def test_json_brief_is_a_versioned_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "people.db"
    person_id = _seed(db_path)

    assert main(["--db", str(db_path), "brief", person_id, "--json"]) == 0

    text = capsys.readouterr().out
    assert text.endswith("\n")
    payload = json.loads(text)
    assert payload["format"] == "people-context-brief"
    assert payload["version"] == 1
    assert payload["person"]["id"] == person_id
    assert [reminder["kind"] for reminder in payload["reminders"]] == ["follow_up", "communication_note"]


def test_output_file_is_owner_only_and_matches_stdout_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "alice.md"
    _seed(db_path)

    assert main(["--db", str(db_path), "brief", "Alice Zhang"]) == 0
    from_stdout = capsys.readouterr().out
    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    # The rendered text already ends in a newline, so the file carries exactly the bytes
    # stdout did; only the wall-clock `Generated:` line differs between the two runs.
    assert _without_generated(output.read_text(encoding="utf-8")) == _without_generated(from_stdout)
    out = capsys.readouterr().out
    assert f"Wrote the brief for Alice Zhang to {output}." in out
    assert "outside the server's disclosure controls" in out


def _without_generated(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith("- **Generated:**")]


def test_pre_existing_file_mode_is_reset_rather_than_retained(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "alice.json"
    _seed(db_path)
    output.write_text("stale\n", encoding="utf-8")
    os.chmod(output, _STALE_FIXTURE_MODE)

    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--json", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(output).st_mode) & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert "stale" not in output.read_text(encoding="utf-8")


def test_destination_symlink_is_replaced_without_touching_its_target(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    target = tmp_path / "unrelated.txt"
    target.write_text("keep me\n", encoding="utf-8")
    output = tmp_path / "alice.md"
    output.symlink_to(target)

    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--output", str(output)]) == 0

    assert target.read_text(encoding="utf-8") == "keep me\n"
    assert not output.is_symlink()
    assert output.read_text(encoding="utf-8").startswith("# Alice Zhang\n")


def test_repeated_brief_over_unchanged_data_is_byte_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _seed(db_path)

    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--json", "--output", str(first)]) == 0
    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--json", "--output", str(second)]) == 0

    # `generated_at` is the only wall-clock field, so equality is asserted on everything else.
    left = json.loads(first.read_text(encoding="utf-8"))
    right = json.loads(second.read_text(encoding="utf-8"))
    left.pop("generated_at")
    right.pop("generated_at")
    assert left == right


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_refuses_to_publish_over_the_active_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
) -> None:
    # Publication replaces the destination's directory entry while SQLite holds the old
    # inode open, so an unguarded write here would silently destroy the whole store.
    db_path = tmp_path / "people.db"
    _seed(db_path)
    before = db_path.read_bytes()
    target = db_path.with_name(db_path.name + suffix)

    assert main(["--db", str(db_path), "brief", "Alice Zhang", "--output", str(target)]) == 2

    assert db_path.read_bytes() == before
    assert "Refusing to write the brief" in capsys.readouterr().err


def test_unknown_person_exits_one_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "nobody.md"
    _seed(db_path)

    assert main(["--db", str(db_path), "brief", "Nobody", "--output", str(output)]) == 1

    assert not output.exists()
    assert "No person found matching 'Nobody'." in capsys.readouterr().err


def test_ambiguous_person_exits_two_with_candidates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, second_person="Alice Zhao")

    assert main(["--db", str(db_path), "brief", "Alice"]) == 2

    assert "Ambiguous match for 'Alice'" in capsys.readouterr().err
