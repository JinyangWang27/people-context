"""Deterministic iCalendar `VTODO` export of active reminders.

This is a read-only projection: it records nothing, mints no audit or changelog rows,
and never reinterprets a stored timestamp. Reminder datetimes are not required to be
timezone-aware by the current write contract, so a naive value is counted and omitted
rather than silently read in the host timezone and moved across a day boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from people_context.app.records.reminders import ListReminders, ListRemindersInput
from people_context.domain.reminder import Reminder, ReminderStatus

PRODUCT_ID = "-//people-context//reminders//EN"
CALENDAR_VERSION = "2.0"

# Only these exact stored recurrence values map to a rule; anything else is omitted and counted.
SUPPORTED_RECURRENCES: dict[str, str] = {
    "yearly": "YEARLY",
    "monthly": "MONTHLY",
    "weekly": "WEEKLY",
}

# RFC 5545 section 3.1: content lines are folded so no line exceeds 75 octets, and a
# continuation line begins with one linear white-space octet that is not part of the value.
_MAX_LINE_OCTETS = 75
_LINE_BREAK = "\r\n"
_CONTINUATION = " "


class ReminderCalendarResult(BaseModel):
    """One rendered calendar plus the non-sensitive counts explaining what it omits."""

    calendar: str
    exported: int = 0
    skipped_undated: int = 0
    skipped_naive_datetime: int = 0
    recurrence_omitted: int = 0


class ExportReminderCalendar:
    """Render active, fully timezone-aware reminders as a deterministic `VCALENDAR`."""

    def __init__(self, reminders: ListReminders) -> None:
        self._reminders = reminders

    def execute(self) -> ReminderCalendarResult:
        """Return the calendar text and the counts of everything it left out."""
        rows: list[tuple[datetime, str, Reminder]] = []
        skipped_undated = 0
        skipped_naive_datetime = 0

        for reminder in self._reminders.execute(ListRemindersInput(status=ReminderStatus.ACTIVE)):
            if reminder.due_at is None:
                skipped_undated += 1
                continue
            if not _is_aware(reminder.due_at) or not _is_aware(reminder.created_at):
                skipped_naive_datetime += 1
                continue
            rows.append((reminder.due_at, reminder.id, reminder))

        # Ids are unique ULIDs, so `(due_at, id)` is a total order independent of read order.
        rows.sort(key=lambda row: (row[0], row[1]))

        lines = [
            "BEGIN:VCALENDAR",
            f"VERSION:{CALENDAR_VERSION}",
            f"PRODID:{PRODUCT_ID}",
            "CALSCALE:GREGORIAN",
        ]
        recurrence_omitted = 0
        for due_at, _reminder_id, reminder in rows:
            todo, omitted = _render_todo(reminder, due_at)
            lines.extend(todo)
            recurrence_omitted += int(omitted)
        lines.append("END:VCALENDAR")

        return ReminderCalendarResult(
            calendar=_render_lines(lines),
            exported=len(rows),
            skipped_undated=skipped_undated,
            skipped_naive_datetime=skipped_naive_datetime,
            recurrence_omitted=recurrence_omitted,
        )


def _render_lines(lines: list[str]) -> str:
    """Join folded content lines with the CRLF break the format requires, including a final one."""
    return "".join(f"{_fold(line)}{_LINE_BREAK}" for line in lines)


def _render_todo(reminder: Reminder, due_at: datetime) -> tuple[list[str], bool]:
    """Render one `VTODO` and report whether an unsupported recurrence rule was omitted.

    A reminder with an unsupported recurrence is still exported as one dated occurrence;
    only its rule is dropped, so the caller counts it separately from a skipped reminder.
    """
    lines = [
        "BEGIN:VTODO",
        f"UID:{_escape_text(reminder.id)}",
        # DTSTAMP comes from stored provenance rather than wall-clock time, so repeated
        # exports of unchanged data are byte-identical.
        f"DTSTAMP:{_format_utc(reminder.created_at)}",
        f"DUE:{_format_utc(due_at)}",
        f"SUMMARY:{_escape_text(reminder.text)}",
    ]
    omitted = False
    if reminder.recurrence:
        frequency = SUPPORTED_RECURRENCES.get(reminder.recurrence)
        if frequency is None:
            omitted = True
        else:
            lines.append(f"RRULE:FREQ={frequency}")
    lines.append("END:VTODO")
    return lines, omitted


def _is_aware(moment: datetime) -> bool:
    """Return whether a datetime carries a usable UTC offset."""
    return moment.tzinfo is not None and moment.utcoffset() is not None


def _format_utc(moment: datetime) -> str:
    """Format an aware datetime as a canonical UTC iCalendar date-time."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _escape_text(value: str) -> str:
    """Escape a TEXT value per RFC 5545 section 3.3.11.

    The backslash is escaped first so the escapes introduced afterwards are not doubled,
    and every line ending is normalized to the literal `\\n` the format defines.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold one content line to 75 octets without ever splitting a UTF-8 character."""
    if len(line.encode("utf-8")) <= _MAX_LINE_OCTETS:
        return line
    chunks: list[str] = []
    current = ""
    used = 0
    budget = _MAX_LINE_OCTETS
    for character in line:
        width = len(character.encode("utf-8"))
        if used + width > budget:
            chunks.append(current)
            current = ""
            used = 0
            # A continuation line spends one octet on the leading white space.
            budget = _MAX_LINE_OCTETS - len(_CONTINUATION.encode("utf-8"))
        current += character
        used += width
    chunks.append(current)
    return f"{_LINE_BREAK}{_CONTINUATION}".join(chunks)
