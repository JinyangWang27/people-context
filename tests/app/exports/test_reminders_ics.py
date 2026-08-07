"""Application policy for the deterministic reminder iCalendar export (M13.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from people_context.app.exports import ExportReminderCalendar, ReminderCalendarResult
from people_context.app.records import ListReminders
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus

CREATED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
DUE = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
BERLIN = timezone(timedelta(hours=2), "CEST")


class _FakeReminderReader:
    """A minimal `RecordReader` that returns a fixed reminder list in a fixed read order.

    The shared record fake sorts before returning, which would hide whether the export
    imposes its own total order and cannot hold naive and aware rows side by side.
    """

    def __init__(self, reminders: list[Reminder]) -> None:
        self.reminders = reminders

    def get_record(self, entity_type: str, entity_id: str) -> Reminder | None:
        return next(
            (
                reminder
                for reminder in self.reminders
                if entity_type == "reminder" and reminder.id == entity_id
            ),
            None,
        )

    def list_reminders(
        self,
        person_id: str | None = None,
        due_before: datetime | None = None,
        status: ReminderStatus | None = ReminderStatus.ACTIVE,
    ) -> list[Reminder]:
        return [
            reminder
            for reminder in self.reminders
            if (person_id is None or reminder.person_id == person_id)
            and (status is None or reminder.status == status)
        ]


def _reminder(
    *,
    text: str = "Send the notes",
    due_at: datetime | None = DUE,
    created_at: datetime = CREATED,
    recurrence: str | None = None,
    kind: ReminderKind = ReminderKind.FOLLOW_UP,
    status: ReminderStatus = ReminderStatus.ACTIVE,
) -> Reminder:
    return Reminder(
        person_id="person-1",
        text=text,
        kind=kind,
        due_at=due_at,
        created_at=created_at,
        recurrence=recurrence,
        status=status,
    )


def _export(*reminders: Reminder) -> ReminderCalendarResult:
    reader = _FakeReminderReader(list(reminders))
    return ExportReminderCalendar(ListReminders(reader)).execute()


def _content_lines(calendar: str) -> list[str]:
    """Return unfolded content lines, dropping the trailing empty element after the final CRLF."""
    unfolded = calendar.replace("\r\n ", "")
    assert unfolded.endswith("\r\n")
    return unfolded.split("\r\n")[:-1]


class TestCalendarStructure:
    """The envelope, ordering, and canonical line format."""

    def test_wraps_todos_in_one_calendar_with_crlf_lines(self) -> None:
        result = _export(_reminder())

        assert result.calendar.startswith("BEGIN:VCALENDAR\r\n")
        assert result.calendar.endswith("END:VCALENDAR\r\n")
        assert "\n" not in result.calendar.replace("\r\n", "")
        assert _content_lines(result.calendar) == [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//people-context//reminders//EN",
            "CALSCALE:GREGORIAN",
            "BEGIN:VTODO",
            f"UID:{_content_uid(result.calendar)}",
            "DTSTAMP:20260102T030405Z",
            "DUE:20260601T123000Z",
            "SUMMARY:Send the notes",
            "END:VTODO",
            "END:VCALENDAR",
        ]

    def test_empty_store_still_produces_a_valid_empty_calendar(self) -> None:
        result = _export()

        assert _content_lines(result.calendar) == [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//people-context//reminders//EN",
            "CALSCALE:GREGORIAN",
            "END:VCALENDAR",
        ]
        assert result.exported == 0

    def test_orders_by_due_date_then_id_regardless_of_read_order(self) -> None:
        later = _reminder(text="Later", due_at=DUE + timedelta(days=1))
        earlier = _reminder(text="Earlier", due_at=DUE)

        result = _export(later, earlier)

        summaries = [line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:")]
        assert summaries == ["SUMMARY:Earlier", "SUMMARY:Later"]

    def test_same_due_date_breaks_ties_by_reminder_id(self) -> None:
        first = _reminder(text="First")
        second = _reminder(text="Second")
        ordered = sorted([first, second], key=lambda reminder: reminder.id)

        result = _export(*reversed(ordered))

        summaries = [line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:")]
        assert summaries == [f"SUMMARY:{reminder.text}" for reminder in ordered]

    def test_repeated_export_of_unchanged_data_is_byte_identical(self) -> None:
        reminders = [_reminder(text="One"), _reminder(text="Two", due_at=DUE + timedelta(hours=3))]

        first = _export(*reminders)
        second = _export(*reminders)

        assert first.calendar == second.calendar
        # DTSTAMP is stored provenance, never wall-clock time, which is what makes this hold.
        assert first.calendar.count("DTSTAMP:20260102T030405Z") == 2


def _content_uid(calendar: str) -> str:
    line = next(line for line in _content_lines(calendar) if line.startswith("UID:"))
    return line.removeprefix("UID:")


class TestTimeNormalization:
    """Aware timestamps are converted; naive ones are never guessed at."""

    def test_non_utc_offsets_are_converted_to_utc(self) -> None:
        result = _export(
            _reminder(
                due_at=datetime(2026, 6, 1, 14, 30, tzinfo=BERLIN),
                created_at=datetime(2026, 1, 2, 5, 4, 5, tzinfo=BERLIN),
            )
        )

        assert "DUE:20260601T123000Z" in result.calendar
        assert "DTSTAMP:20260102T030405Z" in result.calendar
        assert result.exported == 1

    def test_undated_reminder_is_counted_and_omitted(self) -> None:
        result = _export(_reminder(due_at=None, kind=ReminderKind.COMMUNICATION_NOTE))

        assert result.skipped_undated == 1
        assert result.skipped_naive_datetime == 0
        assert result.exported == 0
        assert "BEGIN:VTODO" not in result.calendar

    def test_naive_due_at_is_counted_and_omitted(self) -> None:
        result = _export(_reminder(due_at=datetime(2026, 6, 1, 12, 30)))

        assert result.skipped_naive_datetime == 1
        assert result.skipped_undated == 0
        assert result.exported == 0
        assert "BEGIN:VTODO" not in result.calendar

    def test_naive_created_at_is_counted_and_omitted(self) -> None:
        result = _export(_reminder(created_at=datetime(2026, 1, 2, 3, 4, 5)))

        assert result.skipped_naive_datetime == 1
        assert result.exported == 0
        assert "BEGIN:VTODO" not in result.calendar

    def test_undated_takes_precedence_over_a_naive_created_at(self) -> None:
        result = _export(
            _reminder(
                due_at=None,
                created_at=datetime(2026, 1, 2, 3, 4, 5),
                kind=ReminderKind.COMMUNICATION_NOTE,
            )
        )

        assert (result.skipped_undated, result.skipped_naive_datetime) == (1, 0)

    def test_completed_reminders_are_not_exported_or_counted(self) -> None:
        result = _export(_reminder(status=ReminderStatus.COMPLETED))

        assert result == ReminderCalendarResult(calendar=result.calendar, exported=0)
        assert "BEGIN:VTODO" not in result.calendar


class TestRecurrence:
    """Only the three exact stored values map to a rule."""

    def test_supported_values_map_to_their_frequency(self) -> None:
        for value, frequency in (("yearly", "YEARLY"), ("monthly", "MONTHLY"), ("weekly", "WEEKLY")):
            result = _export(_reminder(kind=ReminderKind.OCCASION, recurrence=value))

            assert f"RRULE:FREQ={frequency}" in result.calendar
            assert result.recurrence_omitted == 0
            assert result.exported == 1

    def test_recurring_todo_anchors_its_rule_with_a_dtstart(self) -> None:
        # RFC 5545 generates the recurrence set from DTSTART; without one a conforming
        # consumer has no anchor and may import a single occurrence instead of a series.
        result = _export(_reminder(kind=ReminderKind.OCCASION, recurrence="yearly"))

        lines = _content_lines(result.calendar)
        assert "DTSTART:20260601T123000Z" in lines
        assert lines.index("DTSTART:20260601T123000Z") < lines.index("RRULE:FREQ=YEARLY")
        # DUE must not precede DTSTART; the same stored instant satisfies that exactly.
        assert lines.index("DTSTART:20260601T123000Z") < lines.index("DUE:20260601T123000Z")

    def test_non_recurring_todo_has_no_dtstart(self) -> None:
        assert "DTSTART" not in _export(_reminder()).calendar

    def test_omitted_recurrence_leaves_a_plain_dated_todo(self) -> None:
        result = _export(_reminder(kind=ReminderKind.OCCASION, recurrence="fortnightly"))

        assert "DTSTART" not in result.calendar
        assert "RRULE" not in result.calendar
        assert result.exported == 1

    def test_unsupported_recurrence_still_exports_one_dated_occurrence(self) -> None:
        result = _export(_reminder(kind=ReminderKind.OCCASION, recurrence="every other thursday"))

        assert result.exported == 1
        assert result.recurrence_omitted == 1
        assert (result.skipped_undated, result.skipped_naive_datetime) == (0, 0)
        assert "RRULE" not in result.calendar
        assert "DUE:20260601T123000Z" in result.calendar

    def test_recurrence_matching_is_exact_and_case_sensitive(self) -> None:
        result = _export(_reminder(kind=ReminderKind.OCCASION, recurrence="Yearly"))

        assert "RRULE" not in result.calendar
        assert result.recurrence_omitted == 1

    def test_absent_recurrence_emits_no_rule_and_no_count(self) -> None:
        result = _export(_reminder(recurrence=None))

        assert "RRULE" not in result.calendar
        assert result.recurrence_omitted == 0


class TestTextEscapingAndFolding:
    """RFC 5545 TEXT escaping and 75-octet folding."""

    def test_escapes_backslash_semicolon_comma_and_newlines(self) -> None:
        result = _export(_reminder(text="a\\b;c,d\ne\r\nf\rg"))

        summary = next(line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:"))
        assert summary == "SUMMARY:a\\\\b\\;c\\,d\\ne\\nf\\ng"

    def test_drops_control_characters_that_text_values_exclude(self) -> None:
        # A stored NUL or form feed is accepted by the write contract but is not a
        # TSAFE-CHAR; emitting one can make a strict consumer reject the whole file.
        result = _export(_reminder(text="a\x00b\x0cc\x1fd\x7fe"))

        summary = next(line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:"))
        assert summary == "SUMMARY:abcde"

    def test_keeps_tab_which_text_values_allow(self) -> None:
        result = _export(_reminder(text="a\tb"))

        summary = next(line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:"))
        assert summary == "SUMMARY:a\tb"

    def test_control_stripping_preserves_real_line_endings(self) -> None:
        result = _export(_reminder(text="a\r\nb\x00c"))

        summary = next(line for line in _content_lines(result.calendar) if line.startswith("SUMMARY:"))
        assert summary == "SUMMARY:a\\nbc"

    def test_folds_long_lines_at_seventy_five_octets_with_a_leading_space(self) -> None:
        result = _export(_reminder(text="x" * 200))

        raw_lines = result.calendar.split("\r\n")[:-1]
        assert all(len(line.encode("utf-8")) <= 75 for line in raw_lines)
        assert any(line.startswith(" ") for line in raw_lines)
        # Unfolding restores exactly one logical content line.
        assert f"SUMMARY:{'x' * 200}" in _content_lines(result.calendar)

    def test_folding_never_splits_a_multi_byte_character(self) -> None:
        result = _export(_reminder(text="漢" * 60))

        for line in result.calendar.split("\r\n")[:-1]:
            assert len(line.encode("utf-8")) <= 75
        assert f"SUMMARY:{'漢' * 60}" in _content_lines(result.calendar)

    def test_uid_comes_from_the_reminder_id(self) -> None:
        reminder = _reminder()

        result = _export(reminder)

        assert f"UID:{reminder.id}" in _content_lines(result.calendar)
