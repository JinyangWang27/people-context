"""CLI behaviour for the `pctx upcoming` date report."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.records import RecordFact, RecordFactInput, SetReminder, SetReminderInput
from people_context.domain.person import Person
from people_context.domain.reminder import ReminderKind
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock


def _in_days(days: int) -> date:
    return datetime.now(UTC).date() + timedelta(days=days)


def _partial(day: date) -> str:
    return f"--{day.month:02d}-{day.day:02d}"


def _seed(db_path: Path) -> dict[str, Person]:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        audit = SqliteAuditLog(conn)
        clock = SystemClock()
        people = {
            "alice": Person(canonical_name="Alice"),
            "bob": Person(canonical_name="Bob"),
            "carol": Person(canonical_name="Carol"),
            "vague": Person(canonical_name="Vague"),
        }
        for person in people.values():
            repo.save_person(person)
        facts = RecordFact(repo, records, audit, clock)
        facts.execute(RecordFactInput(person_id=people["alice"].id, predicate="birthday", value=_partial(_in_days(5))))
        facts.execute(RecordFactInput(person_id=people["bob"].id, predicate="birthday", value=_partial(_in_days(200))))
        facts.execute(
            RecordFactInput(
                person_id=people["carol"].id,
                predicate="birthday",
                value=_partial(_in_days(5)),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        facts.execute(RecordFactInput(person_id=people["vague"].id, predicate="birthday", value="sometime in spring"))
        reminders = SetReminder(repo, records, audit, clock)
        reminders.execute(
            SetReminderInput(
                person_id=people["bob"].id,
                text="return the borrowed book",
                kind=ReminderKind.FOLLOW_UP,
                due_at=datetime.combine(_in_days(3), datetime.min.time(), tzinfo=UTC),
            )
        )
        return people
    finally:
        conn.close()


def test_upcoming_lists_reminders_and_ordinary_birthdays_in_date_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming"])

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert lines[0].split() == ["DATE", "KIND", "NAME", "LABEL"]
    assert [line.split()[1:3] for line in lines[1:3]] == [["reminder", "Bob"], ["birthday", "Alice"]]
    assert lines[1].startswith(_in_days(3).isoformat())
    assert lines[2].startswith(_in_days(5).isoformat())
    assert "Carol" not in out
    assert "Skipped 1 birthday fact(s) with an unrecognized date value." in out


def test_upcoming_widens_to_a_longer_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming", "--window-days", "366"])

    out = capsys.readouterr().out
    assert code == 0
    assert [line.split()[2] for line in out.splitlines() if line.strip()][1:4] == ["Bob", "Alice", "Bob"]


def test_upcoming_accepts_a_resolvable_person_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming", "--person", "Alice"])

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert [line.split()[1:3] for line in lines[1:]] == [["birthday", "Alice"]]
    # The other person's unparseable fact belongs to a filtered-out person.
    assert "Skipped" not in out


def test_upcoming_reports_nothing_inside_a_zero_day_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming", "--window-days", "0", "--person", "Alice"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "No upcoming dates."


def test_upcoming_refuses_an_unknown_person(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming", "--person", "Nobody At All"])

    assert code == 1
    assert "No person found matching 'Nobody At All'." in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [["--window-days", "-1"], ["--window-days", "367"]])
def test_upcoming_refuses_out_of_range_windows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "upcoming", *arguments])

    captured = capsys.readouterr()
    assert code == 2
    assert "window_days must be between 0 and 366" in captured.err
    assert captured.out == ""
