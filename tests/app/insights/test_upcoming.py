"""Application policy for the upcoming-dates report, against fakes and a fixed clock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from people_context.app.insights import (
    BIRTHDAY_LABEL,
    ListUpcomingDates,
    UpcomingDateKind,
    UpcomingDatesError,
)
from people_context.app.records import ListReminders
from people_context.domain.fact import Fact
from people_context.domain.person import Person
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import Provenance, Sensitivity
from tests.app.fakes import FakeClock, FakeContextReader, FakePeopleRepository, FakeRecordStore

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
PROVENANCE = Provenance(source="test")


class _Harness:
    """A wired `ListUpcomingDates` plus the fakes behind it."""

    def __init__(self, now: datetime = NOW) -> None:
        self.people = FakePeopleRepository()
        self.context = FakeContextReader()
        self.records = FakeRecordStore()
        self.clock = FakeClock(now)
        self.use_case = ListUpcomingDates(
            self.context,
            ListReminders(self.records),
            self.people,
            self.clock,
        )

    def person(self, name: str, *, deleted: bool = False) -> Person:
        person = Person(canonical_name=name, deleted_at=NOW if deleted else None)
        self.people.save_person(person)
        return person

    def birthday(
        self,
        person: Person,
        value: str,
        *,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
        predicate: str = "birthday",
    ) -> Fact:
        fact = Fact(
            person_id=person.id,
            predicate=predicate,
            value=value,
            sensitivity=sensitivity,
            provenance=PROVENANCE,
        )
        self.context.facts.append(fact)
        return fact

    def reminder(
        self,
        person_id: str,
        text: str,
        due_at: datetime | None,
        *,
        status: ReminderStatus = ReminderStatus.ACTIVE,
    ) -> Reminder:
        reminder = Reminder(
            person_id=person_id,
            text=text,
            kind=ReminderKind.FOLLOW_UP,
            due_at=due_at,
            status=status,
        )
        self.records.records[("reminder", reminder.id)] = reminder
        return reminder


def test_window_includes_both_inclusive_boundaries_and_excludes_just_outside() -> None:
    harness = _Harness()
    today = harness.person("Today")
    last_day = harness.person("Last Day")
    outside = harness.person("Outside")
    harness.birthday(today, "--06-01")
    harness.birthday(last_day, "--07-01")
    harness.birthday(outside, "--07-02")

    result = harness.use_case.execute(window_days=30)

    assert [(entry.name, entry.date) for entry in result.entries] == [
        ("Today", date(2026, 6, 1)),
        ("Last Day", date(2026, 7, 1)),
    ]
    assert result.skipped_unparseable == 0


def test_zero_window_reports_only_today() -> None:
    harness = _Harness()
    today = harness.person("Today")
    tomorrow = harness.person("Tomorrow")
    harness.birthday(today, "1990-06-01")
    harness.birthday(tomorrow, "1990-06-02")

    result = harness.use_case.execute(window_days=0)

    assert [entry.name for entry in result.entries] == ["Today"]
    assert result.entries[0].kind is UpcomingDateKind.BIRTHDAY
    assert result.entries[0].label == BIRTHDAY_LABEL


def test_full_and_partial_birthdays_both_project_annually() -> None:
    harness = _Harness()
    full = harness.person("Full")
    partial = harness.person("Partial")
    harness.birthday(full, "1984-06-10")
    harness.birthday(partial, "--06-10")

    result = harness.use_case.execute(window_days=30)

    assert [(entry.name, entry.date) for entry in result.entries] == [
        ("Full", date(2026, 6, 10)),
        ("Partial", date(2026, 6, 10)),
    ]


def test_a_past_birthday_rolls_into_the_next_year() -> None:
    harness = _Harness(now=datetime(2026, 12, 20, 9, 0, tzinfo=UTC))
    person = harness.person("Rollover")
    harness.birthday(person, "--01-05")

    result = harness.use_case.execute(window_days=30)

    assert [entry.date for entry in result.entries] == [date(2027, 1, 5)]


def test_leap_day_waits_for_the_next_real_leap_day() -> None:
    common_year = _Harness(now=datetime(2026, 2, 1, 9, 0, tzinfo=UTC))
    leap_year = _Harness(now=datetime(2028, 2, 1, 9, 0, tzinfo=UTC))
    for harness in (common_year, leap_year):
        harness.birthday(harness.person("Leaper"), "2000-02-29")

    common = common_year.use_case.execute(window_days=60)
    leap = leap_year.use_case.execute(window_days=60)

    # 2026 has no 29 February, and the day is never coerced to the 28th or to 1 March.
    assert common.entries == []
    assert [entry.date for entry in leap.entries] == [date(2028, 2, 29)]


def test_leap_day_is_reported_when_the_window_reaches_the_next_leap_year() -> None:
    harness = _Harness(now=datetime(2027, 3, 1, 9, 0, tzinfo=UTC))
    harness.birthday(harness.person("Leaper"), "--02-29")

    result = harness.use_case.execute(window_days=366)

    assert [entry.date for entry in result.entries] == [date(2028, 2, 29)]


def test_reminders_use_the_stored_calendar_date_and_their_text() -> None:
    harness = _Harness()
    person = harness.person("Dana")
    harness.reminder(person.id, "send the signed contract", datetime(2026, 6, 15, 8, 0, tzinfo=UTC))

    result = harness.use_case.execute(window_days=30)

    assert [(entry.kind, entry.date, entry.label) for entry in result.entries] == [
        (UpcomingDateKind.REMINDER, date(2026, 6, 15), "send the signed contract")
    ]


def test_a_naive_due_at_keeps_its_own_calendar_day() -> None:
    harness = _Harness()
    person = harness.person("Dana")
    # A naive 23:30 must not be shifted into the next day by a host-timezone reading.
    harness.reminder(person.id, "call back", datetime(2026, 6, 15, 23, 30))

    result = harness.use_case.execute(window_days=30)

    assert [entry.date for entry in result.entries] == [date(2026, 6, 15)]


def test_a_non_utc_offset_keeps_its_own_calendar_day() -> None:
    harness = _Harness()
    person = harness.person("Dana")
    # 23:30-05:00 is 04:30 UTC the next day; the report keeps the stored calendar day.
    harness.reminder(
        person.id,
        "call back",
        datetime(2026, 6, 15, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
    )

    result = harness.use_case.execute(window_days=30)

    assert [entry.date for entry in result.entries] == [date(2026, 6, 15)]


def test_undated_completed_and_past_reminders_are_not_reported() -> None:
    harness = _Harness()
    person = harness.person("Dana")
    harness.reminder(person.id, "undated note", None)
    harness.reminder(person.id, "already past", datetime(2026, 5, 31, 8, 0, tzinfo=UTC))
    harness.reminder(
        person.id,
        "already done",
        datetime(2026, 6, 10, 8, 0, tzinfo=UTC),
        status=ReminderStatus.COMPLETED,
    )

    result = harness.use_case.execute(window_days=30)

    assert result.entries == []
    assert result.skipped_unparseable == 0


def test_elevated_birthday_facts_produce_no_entry_and_no_skip_count() -> None:
    harness = _Harness()
    sensitive = harness.person("Sensitive")
    restricted = harness.person("Restricted")
    harness.birthday(sensitive, "--06-10", sensitivity=Sensitivity.SENSITIVE)
    # An elevated fact must not even reveal itself through the skip counter.
    harness.birthday(restricted, "sometime in June", sensitivity=Sensitivity.RESTRICTED)

    result = harness.use_case.execute(window_days=30)

    assert result.entries == []
    assert result.skipped_unparseable == 0


def test_unparseable_ordinary_birthday_values_are_counted_not_guessed() -> None:
    harness = _Harness()
    person = harness.person("Vague")
    for value in ("sometime in June", "10/06/1984", "1985-02-29", "--02-30", "--6-10", ""):
        harness.birthday(person, value)

    result = harness.use_case.execute(window_days=366)

    assert result.entries == []
    assert result.skipped_unparseable == 6


def test_non_birthday_predicates_are_ignored_entirely() -> None:
    harness = _Harness()
    person = harness.person("Ravi")
    harness.birthday(person, "--06-10", predicate="anniversary")
    harness.birthday(person, "not a date", predicate="joined_on")

    result = harness.use_case.execute(window_days=30)

    assert result.entries == []
    assert result.skipped_unparseable == 0


def test_soft_deleted_people_are_skipped_with_their_reminders() -> None:
    harness = _Harness()
    deleted = harness.person("Deleted", deleted=True)
    harness.birthday(deleted, "--06-10")
    harness.reminder(deleted.id, "follow up", datetime(2026, 6, 12, 8, 0, tzinfo=UTC))

    result = harness.use_case.execute(window_days=30)

    assert result.entries == []
    assert result.skipped_unparseable == 0


def test_reminders_for_missing_people_are_skipped() -> None:
    harness = _Harness()
    harness.reminder("PERSON-NEVER-STORED", "follow up", datetime(2026, 6, 12, 8, 0, tzinfo=UTC))

    assert harness.use_case.execute(window_days=30).entries == []


def test_person_filter_reports_only_that_person() -> None:
    harness = _Harness()
    wanted = harness.person("Wanted")
    other = harness.person("Other")
    harness.birthday(wanted, "--06-10")
    harness.birthday(other, "--06-11")
    harness.reminder(other.id, "not mine", datetime(2026, 6, 12, 8, 0, tzinfo=UTC))

    result = harness.use_case.execute(window_days=30, person_id=wanted.id)

    assert [entry.name for entry in result.entries] == ["Wanted"]


def test_a_deleted_or_unknown_person_filter_reports_nothing() -> None:
    harness = _Harness()
    deleted = harness.person("Deleted", deleted=True)
    harness.birthday(deleted, "--06-10")

    assert harness.use_case.execute(person_id=deleted.id).entries == []
    assert harness.use_case.execute(person_id="PERSON-NEVER-STORED").entries == []


def test_entries_sort_by_date_then_name_then_kind() -> None:
    harness = _Harness()
    zoe = harness.person("Zoe")
    adam = harness.person("Adam")
    harness.birthday(zoe, "--06-10")
    harness.birthday(adam, "--06-10")
    harness.reminder(adam.id, "a reminder on the same day", datetime(2026, 6, 10, 8, 0, tzinfo=UTC))
    harness.reminder(zoe.id, "an earlier day", datetime(2026, 6, 5, 8, 0, tzinfo=UTC))

    result = harness.use_case.execute(window_days=30)

    assert [(entry.name, entry.date, entry.kind.value) for entry in result.entries] == [
        ("Zoe", date(2026, 6, 5), "reminder"),
        ("Adam", date(2026, 6, 10), "birthday"),
        ("Adam", date(2026, 6, 10), "reminder"),
        ("Zoe", date(2026, 6, 10), "birthday"),
    ]


def test_the_report_is_deterministic_across_repeated_calls() -> None:
    harness = _Harness()
    for name in ("Ada", "Bo", "Cy"):
        harness.birthday(harness.person(name), "--06-10")

    first = harness.use_case.execute(window_days=30)
    second = harness.use_case.execute(window_days=30)

    assert first == second


@pytest.mark.parametrize("window_days", [-1, 367])
def test_out_of_range_window_days_is_refused(window_days: int) -> None:
    harness = _Harness()

    with pytest.raises(UpcomingDatesError, match="window_days must be between 0 and 366"):
        harness.use_case.execute(window_days=window_days)


def test_the_clock_rather_than_the_host_date_decides_the_window() -> None:
    harness = _Harness(now=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
    person = harness.person("Later")
    harness.birthday(person, "--08-20")

    assert harness.use_case.execute(window_days=30).entries == []

    harness.clock.set(datetime(2026, 8, 1, 9, 0, tzinfo=UTC))

    assert [entry.date for entry in harness.use_case.execute(window_days=30).entries] == [date(2026, 8, 20)]
