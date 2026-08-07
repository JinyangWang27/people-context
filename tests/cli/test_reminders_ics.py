"""CLI behaviour for the `pctx reminders-ics` calendar export (M13.3)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.records import SetReminder, SetReminderInput
from people_context.cli import main
from people_context.domain.person import Person
from people_context.domain.reminder import Reminder, ReminderKind

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
_DUE = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
# Matches `tests/adapters/filesystem/test_private_file.py`: the stale-destination fixture must
# differ from 0o600 so a mode-retaining `O_TRUNC` write is caught, but must grant nothing to
# group or other, because CodeQL `security-extended` reports `py/overly-permissive-file` for any
# non-owner bit even in a test. 0o700 exercises the identical defect, because `fchmod` sets an
# absolute mode rather than clearing selected bits.
_STALE_FIXTURE_MODE = 0o700


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _seed(db_path: Path) -> None:
    """Create one person with a dated follow-up, an undated note, and an odd recurrence."""
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        person = Person(canonical_name="Alice")
        repository.save_person(person)
        set_reminder = SetReminder(repository, records, SqliteAuditLog(conn), _Clock())
        set_reminder.execute(
            SetReminderInput(
                person_id=person.id,
                text="Send the notes",
                kind=ReminderKind.FOLLOW_UP,
                due_at=_DUE,
            )
        )
        set_reminder.execute(
            SetReminderInput(
                person_id=person.id,
                text="Prefers short messages",
                kind=ReminderKind.COMMUNICATION_NOTE,
            )
        )
        set_reminder.execute(
            SetReminderInput(
                person_id=person.id,
                text="Team offsite",
                kind=ReminderKind.OCCASION,
                due_at=_DUE + timedelta(days=1),
                recurrence="every other thursday",
            )
        )
    finally:
        conn.close()


def test_writes_a_canonical_owner_only_calendar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "reminders.ics"
    _seed(db_path)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    text = output.read_bytes().decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VTODO") == 2
    assert "DUE:20260601T123000Z" in text
    assert "DTSTAMP:20260304T050600Z" in text
    assert "RRULE" not in text

    out = capsys.readouterr().out
    assert "Wrote 2 reminder(s)" in out
    assert "Skipped 1 reminder(s) without a due date." in out
    assert "Exported 1 reminder(s) with the recurrence rule omitted." in out
    assert "outside the server's disclosure controls" in out


def test_pre_existing_file_mode_is_reset_rather_than_retained(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "reminders.ics"
    _seed(db_path)
    output.write_text("stale\n", encoding="utf-8")
    os.chmod(output, _STALE_FIXTURE_MODE)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(output).st_mode) & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert "stale" not in output.read_text(encoding="utf-8")


def test_repeated_export_of_unchanged_data_is_byte_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    first = tmp_path / "first.ics"
    second = tmp_path / "second.ics"
    _seed(db_path)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(first)]) == 0
    assert main(["--db", str(db_path), "reminders-ics", "--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()


def test_stored_naive_timestamps_are_skipped_rather_than_localized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The write contract still accepts naive datetimes and SQLite round-trips them naive,
    # so the export must count and omit the row instead of reading it in the host timezone.
    db_path = tmp_path / "people.db"
    output = tmp_path / "reminders.ics"
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        person = Person(canonical_name="Alice")
        repository.save_person(person)
        SqliteRecordStore(conn).save_reminder(
            Reminder(
                person_id=person.id,
                text="Legacy row",
                kind=ReminderKind.FOLLOW_UP,
                due_at=datetime(2026, 6, 1, 12, 30),
                created_at=_NOW,
            )
        )
    finally:
        conn.close()

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    assert "BEGIN:VTODO" not in output.read_bytes().decode("utf-8")
    assert "Skipped 1 reminder(s) whose stored timestamps have no timezone." in capsys.readouterr().out


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

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(target)]) == 2

    assert db_path.read_bytes() == before
    assert "Refusing to write the reminder calendar" in capsys.readouterr().err


def test_refuses_an_indirect_path_to_the_active_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    before = db_path.read_bytes()
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(link / "people.db")]) == 2

    assert db_path.read_bytes() == before
    assert "Refusing to write the reminder calendar" in capsys.readouterr().err


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_refuses_the_resolved_target_of_a_symlinked_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
) -> None:
    # SQLite follows a symlinked `--db`, so the entry holding the data is the resolved
    # one; naming that target as the output must be refused even though the spellings
    # differ, or the store is replaced with calendar text.
    real = tmp_path / "real.db"
    _seed(real)
    link = tmp_path / "link.db"
    link.symlink_to(real)
    before = real.read_bytes()
    target = real.with_name(real.name + suffix)

    assert main(["--db", str(link), "reminders-ics", "--output", str(target)]) == 2

    assert real.read_bytes() == before
    assert "Refusing to write the reminder calendar" in capsys.readouterr().err


def test_refuses_the_symlink_spelling_of_a_symlinked_database(tmp_path: Path) -> None:
    real = tmp_path / "real.db"
    _seed(real)
    link = tmp_path / "link.db"
    link.symlink_to(real)
    before = real.read_bytes()

    assert main(["--db", str(link), "reminders-ics", "--output", str(link)]) == 2

    assert real.read_bytes() == before
    assert link.is_symlink()


def test_an_output_symlink_pointing_elsewhere_is_still_allowed(tmp_path: Path) -> None:
    # Publication replaces the output entry instead of following it, so this is harmless
    # and the guard must not become broad enough to refuse it.
    db_path = tmp_path / "people.db"
    _seed(db_path)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    output = tmp_path / "reminders.ics"
    output.symlink_to(unrelated)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    assert unrelated.read_text(encoding="utf-8") == "keep me\n"
    assert not output.is_symlink()
    assert output.read_bytes().decode("utf-8").startswith("BEGIN:VCALENDAR\r\n")


def test_a_neighbouring_file_beside_the_database_is_still_allowed(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.db.ics"
    _seed(db_path)

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    assert output.read_bytes().decode("utf-8").startswith("BEGIN:VCALENDAR\r\n")


def test_missing_destination_directory_reports_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    missing = tmp_path / "no-such-directory" / "reminders.ics"

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(missing)]) == 1

    assert not missing.exists()
    assert "Cannot write the reminder calendar" in capsys.readouterr().err


def test_empty_store_writes_an_empty_calendar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "reminders.ics"
    open_db(db_path).close()

    assert main(["--db", str(db_path), "reminders-ics", "--output", str(output)]) == 0

    text = output.read_bytes().decode("utf-8")
    assert "BEGIN:VTODO" not in text
    assert text.endswith("END:VCALENDAR\r\n")
    assert "Wrote 0 reminder(s)" in capsys.readouterr().out
