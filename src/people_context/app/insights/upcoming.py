"""Upcoming birthday and reminder dates projected over an inclusive window."""

from __future__ import annotations

import re
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from people_context.app.records.reminders import ListReminders, ListRemindersInput
from people_context.domain.person import Person
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.domain.shared import Sensitivity, normalize_name
from people_context.ports.clock import Clock
from people_context.ports.context import PersonContextReader
from people_context.ports.repository import PersonReader

MIN_WINDOW_DAYS = 0
MAX_WINDOW_DAYS = 366
DEFAULT_WINDOW_DAYS = 30

BIRTHDAY_PREDICATE = "birthday"
BIRTHDAY_LABEL = "Birthday"
ORDINARY_SENSITIVITIES = frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL})

_FULL_BIRTHDAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PARTIAL_BIRTHDAY = re.compile(r"^--(\d{2})-(\d{2})$")
# 29 February is never coerced to a neighbouring day, so the next occurrence can be eight
# calendar years away (2096 -> 2104). Nine candidate years always contain one real occurrence.
_MAX_PROJECTION_YEARS = 9
# Any leap year works as a calendar for validating a month/day pair that carries no year.
_MONTH_DAY_CALENDAR_YEAR = 2000


class UpcomingDatesError(ValueError):
    """Raised when an upcoming-dates parameter falls outside its documented range."""


class UpcomingDateKind(StrEnum):
    """Which stored record produced an upcoming date."""

    BIRTHDAY = "birthday"
    REMINDER = "reminder"


class UpcomingDateEntry(BaseModel):
    """One dated occurrence inside the requested window."""

    person_id: str
    name: str
    kind: UpcomingDateKind
    date: date
    label: str


class UpcomingDatesResult(BaseModel):
    """The ordered window report plus the count of unusable birthday values."""

    entries: list[UpcomingDateEntry] = Field(default_factory=list)
    skipped_unparseable: int = 0


# date, normalized name, person id, kind rank, label, and source record id.
_SortKey = tuple[date, str, str, int, str, str]

_KIND_ORDER: dict[UpcomingDateKind, int] = {
    UpcomingDateKind.BIRTHDAY: 0,
    UpcomingDateKind.REMINDER: 1,
}


class ListUpcomingDates:
    """Project ordinary birthday facts and dated active reminders into an inclusive window.

    Every time-dependent decision uses the injected clock, so the report is deterministic
    under a fake clock. Sensitive and restricted facts are invisible: they contribute
    neither entries nor skip counts, so the report never signals that they exist.
    """

    def __init__(
        self,
        context: PersonContextReader,
        reminders: ListReminders,
        people: PersonReader,
        clock: Clock,
    ) -> None:
        self._context = context
        self._reminders = reminders
        self._people = people
        self._clock = clock

    def execute(
        self,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        person_id: str | None = None,
    ) -> UpcomingDatesResult:
        """Return dated entries in `[today, today + window_days]`, oldest first."""
        _validate(window_days)
        today = self._clock.now().date()
        window_end = today + timedelta(days=window_days)
        targets = self._targets(person_id)
        rows: list[tuple[_SortKey, UpcomingDateEntry]] = []
        skipped_unparseable = 0

        for person in targets.values():
            entries, skipped = self._birthdays(person, today, window_end)
            rows.extend(entries)
            skipped_unparseable += skipped
        rows.extend(self._reminder_entries(targets, person_id, today, window_end))

        rows.sort(key=lambda row: row[0])
        return UpcomingDatesResult(
            entries=[entry for _key, entry in rows],
            skipped_unparseable=skipped_unparseable,
        )

    def _targets(self, person_id: str | None) -> dict[str, Person]:
        """Return the active people the report may name, keyed by id.

        A missing or soft-deleted person yields no target, which is what makes both a
        deleted filter argument and a reminder pointing at a deleted person disappear.
        """
        if person_id is None:
            return {person.id: person for person in self._people.list_people()}
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return {}
        return {person.id: person}

    def _birthdays(
        self,
        person: Person,
        today: date,
        window_end: date,
    ) -> tuple[list[tuple[_SortKey, UpcomingDateEntry]], int]:
        rows: list[tuple[_SortKey, UpcomingDateEntry]] = []
        skipped = 0
        for fact in self._context.list_facts(person.id):
            if fact.sensitivity not in ORDINARY_SENSITIVITIES or fact.predicate != BIRTHDAY_PREDICATE:
                continue
            month_day = _parse_birthday(fact.value)
            if month_day is None:
                skipped += 1
                continue
            occurrence = _next_occurrence(month_day, today)
            if occurrence is None or occurrence > window_end:
                continue
            rows.append(
                _row(person, UpcomingDateKind.BIRTHDAY, occurrence, BIRTHDAY_LABEL, fact.id)
            )
        return rows, skipped

    def _reminder_entries(
        self,
        targets: dict[str, Person],
        person_id: str | None,
        today: date,
        window_end: date,
    ) -> list[tuple[_SortKey, UpcomingDateEntry]]:
        rows: list[tuple[_SortKey, UpcomingDateEntry]] = []
        reminders = self._reminders.execute(
            ListRemindersInput(person_id=person_id, status=ReminderStatus.ACTIVE)
        )
        for reminder in reminders:
            person = targets.get(reminder.person_id)
            if person is None:
                continue
            due_on = _due_date(reminder)
            if due_on is None or due_on < today or due_on > window_end:
                continue
            rows.append(
                _row(person, UpcomingDateKind.REMINDER, due_on, reminder.text, reminder.id)
            )
        return rows


def _validate(window_days: int) -> None:
    if window_days < MIN_WINDOW_DAYS or window_days > MAX_WINDOW_DAYS:
        raise UpcomingDatesError(f"window_days must be between {MIN_WINDOW_DAYS} and {MAX_WINDOW_DAYS}")


def _row(
    person: Person,
    kind: UpcomingDateKind,
    occurrence: date,
    label: str,
    source_id: str,
) -> tuple[_SortKey, UpcomingDateEntry]:
    """Pair one entry with a total ordering key that never depends on read order."""
    entry = UpcomingDateEntry(
        person_id=person.id,
        name=person.canonical_name,
        kind=kind,
        date=occurrence,
        label=label,
    )
    key: _SortKey = (
        occurrence,
        normalize_name(person.canonical_name),
        person.id,
        _KIND_ORDER[kind],
        label,
        source_id,
    )
    return key, entry


def _due_date(reminder: Reminder) -> date | None:
    """Return the reminder's stored calendar date, without inventing a timezone.

    Reminder datetimes are not required to be timezone-aware, and this report is a read
    path rather than a write-contract change. Converting a naive value here would silently
    read it in the host timezone and could move it across a day boundary.
    """
    return None if reminder.due_at is None else reminder.due_at.date()


def _parse_birthday(value: str) -> tuple[int, int] | None:
    """Return the `(month, day)` of a full or partial birthday value, if it is a real date."""
    text = value.strip()
    full = _FULL_BIRTHDAY.match(text)
    if full is not None:
        year, month, day = (int(group) for group in full.groups())
        return (month, day) if _is_real_date(year, month, day) else None
    partial = _PARTIAL_BIRTHDAY.match(text)
    if partial is not None:
        month, day = int(partial.group(1)), int(partial.group(2))
        # A leap-year calendar keeps `--02-29` valid while still rejecting `--02-30`.
        return (month, day) if _is_real_date(_MONTH_DAY_CALENDAR_YEAR, month, day) else None
    return None


def _is_real_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _next_occurrence(month_day: tuple[int, int], today: date) -> date | None:
    """Return the earliest real annual occurrence on or after `today`.

    29 February is never coerced to 28 February or 1 March: a common year simply has no
    occurrence, so the search moves on to the next candidate year.
    """
    month, day = month_day
    for offset in range(_MAX_PROJECTION_YEARS):
        try:
            candidate = date(today.year + offset, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None
