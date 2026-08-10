"""WhatsApp plaintext chat-export extraction into narrow staged candidates.

Only the timestamp prefix and the sender label of each exported message are read. Everything
after the sender separator is message body: it is never copied into a candidate, a skip reason,
a log record, or an error, and it is discarded with the rest of the parsed text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from people_context.adapters.importers.email import ImportExtractionError
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractedImport

# One neutral, source-independent interaction summary. Message text, attachment names, and
# system notices are deliberately never retained.
_CHAT_SUMMARY = "WhatsApp chat"
_CHANNEL = "whatsapp"
# A sender label longer than this is not a WhatsApp display name; refusing it keeps a body line
# that happens to look like a message header from becoming candidate text.
_MAX_SENDER_LENGTH = 80
_MIN_PHONE_DIGITS = 7

# Exports carry directional-isolate marks around system notices and attachment lines, and
# locale-specific narrow spaces inside timestamps; both are normalized before matching.
_BIDI_MARKS = "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2068\u2069"
_NARROW_SPACES = "\u00a0\u202f"

_BRACKET_RE = re.compile(r"^\[\s*(?P<date>[^,\]]+?)\s*,\s*(?P<time>[^\]]+?)\s*\]\s*(?P<rest>.*)$")
_DASH_RE = re.compile(r"^(?P<date>[^,]+?)\s*,\s*(?P<time>.+?)\s+-\s+(?P<rest>.*)$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# Both separators must be the same character: a mixed token like `13/02.2025` is not one of the
# accepted forms, and accepting it would let a body line masquerade as a message header.
_NUMERIC_DATE_RE = re.compile(
    r"^(?P<first>\d{1,2})(?P<separator>[/.])(?P<second>\d{1,2})(?P=separator)(?P<year>\d{2}|\d{4})$"
)
# Locales render the meridiem as `AM`, `a.m.`, or the Spanish `a. m.`; the internal space may be
# a narrow or non-breaking one, which `_strip_marks` has already normalized by this point.
_TIME_RE = re.compile(
    r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?: ?(?P<meridiem>[APap])\.? ?[Mm]\.?)?$"
)
_SENDER_RE = re.compile(r"^(?P<sender>[^:]+?):(?:\s|$)")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()./-]*$")


@dataclass
class _Message:
    """One detected exported message, holding no body text."""

    index: int
    date_token: str
    rest: str
    occurred_on: date | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _PersonAccumulator:
    """Batch-local participant deduplicated by normalized sender identity.

    A label that differs from this one is a different identity by construction — a name keys on
    its normalized form and a phone keys on its digits — so there is no alternate-label state.
    """

    ref: str
    name: str
    handle: str | None


class WhatsAppImportExtractor:
    """Read only per-message timestamps and sender labels from a plaintext chat export."""

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
    ) -> ExtractedImport:
        """Extract external participants and one neutral interaction per calendar day."""
        if source_type != "whatsapp":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'whatsapp'")
        if (content is None) == (path is None):
            raise ImportExtractionError(
                "invalid_source",
                "whatsapp import requires exactly one of content or path",
            )
        text = content if content is not None else Path(path or "").read_text(encoding="utf-8")
        messages = _detect_messages(text)
        _resolve_dates(messages)

        self_identities = _self_identities(self_addresses, self_names, self_sender)
        people: list[_PersonAccumulator] = []
        people_by_identity: dict[str, _PersonAccumulator] = {}
        refs_by_day: dict[date, list[str]] = {}
        skipped: list[dict[str, int | str]] = []

        for message in messages:
            if message.occurred_on is None:
                skipped.append({"index": message.index, "reason": message.reason or "invalid_timestamp"})
                continue
            label, reason = _sender_label(message.rest)
            if label is None:
                skipped.append({"index": message.index, "reason": reason or "no_sender"})
                continue
            identity = _identity_key(label)
            if identity in self_identities:
                # Self participation is implicit: no candidate and no participant reference.
                refs_by_day.setdefault(message.occurred_on, [])
                continue
            person = people_by_identity.get(identity)
            if person is None:
                person = _PersonAccumulator(
                    ref=f"whatsapp-person-{len(people) + 1}",
                    name=label,
                    handle=_phone_handle(label),
                )
                people.append(person)
                people_by_identity[identity] = person
            day_refs = refs_by_day.setdefault(message.occurred_on, [])
            if person.ref not in day_refs:
                day_refs.append(person.ref)

        interactions = [
            {
                "type": "interaction",
                "summary": _CHAT_SUMMARY,
                "participant_refs": refs,
                "date": datetime(day.year, day.month, day.day, tzinfo=UTC),
                "channel": _CHANNEL,
                "message_id": None,
            }
            for day, refs in sorted(refs_by_day.items())
            if refs
        ]
        candidates = [_person_candidate(person) for person in people]
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[*candidates, *interactions],
            skipped_cards=skipped,
        )


def _detect_messages(text: str) -> list[_Message]:
    """Return one entry per line that structurally starts an exported message.

    A line that does not carry a complete, well-formed timestamp prefix is a continuation of the
    previous message body and is dropped without being inspected further.
    """
    messages: list[_Message] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _strip_marks(raw_line).strip()
        if not line:
            continue
        for pattern in (_BRACKET_RE, _DASH_RE):
            match = pattern.match(line)
            if match is None:
                continue
            date_token = match.group("date").strip()
            if not _valid_clock(_strip_marks(match.group("time")).strip()):
                continue
            if not (_ISO_DATE_RE.fullmatch(date_token) or _NUMERIC_DATE_RE.fullmatch(date_token)):
                continue
            messages.append(_Message(index=len(messages) + 1, date_token=date_token, rest=match.group("rest")))
            break
    return messages


def _valid_clock(token: str) -> bool:
    """Return whether the token is a real clock time, not merely time-shaped digits.

    Detection depends on this: a body line quoting something like ``[13/02/2025, 99:99] text:``
    must stay a body continuation rather than become a message whose "sender" is quoted content.
    """
    match = _TIME_RE.fullmatch(token)
    if match is None:
        return False
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    if minute > 59 or second > 59:
        return False
    if match.group("meridiem") is not None:
        return 1 <= hour <= 12
    return hour <= 23


def _resolve_dates(messages: list[_Message]) -> None:
    """Assign each message a calendar day, or a stable reason why it has none.

    A WhatsApp export carries no UTC offset, so only the calendar day is meaningful. Numeric
    day/month ordering is locale dependent and is inferred from the whole file rather than
    guessed per line; an export that cannot be resolved unambiguously is skipped.
    """
    deferred: list[tuple[_Message, date | None, date | None]] = []
    day_first_evidence = False
    month_first_evidence = False
    for message in messages:
        iso = _ISO_DATE_RE.fullmatch(message.date_token)
        if iso is not None:
            year, month, day = (int(part) for part in iso.groups())
            message.occurred_on = _calendar_date(year, month, day)
            if message.occurred_on is None:
                message.reason = "invalid_timestamp"
            continue
        numeric = _NUMERIC_DATE_RE.fullmatch(message.date_token)
        if numeric is None:  # pragma: no cover - detection accepts only the two forms above
            message.reason = "invalid_timestamp"
            continue
        first, second, year_token = (int(numeric.group(name)) for name in ("first", "second", "year"))
        year = year_token + 2000 if year_token < 100 else year_token
        as_day_first = _calendar_date(year, second, first)
        as_month_first = _calendar_date(year, first, second)
        if as_day_first is None and as_month_first is None:
            # Impossible in both orders, so it is neither a usable date nor usable evidence.
            message.reason = "invalid_timestamp"
            continue
        # Only a token that is a real date in exactly one order says anything about the locale.
        day_first_evidence = day_first_evidence or as_month_first is None
        month_first_evidence = month_first_evidence or as_day_first is None
        deferred.append((message, as_day_first, as_month_first))

    day_first = day_first_evidence and not month_first_evidence
    month_first = month_first_evidence and not day_first_evidence
    for message, as_day_first, as_month_first in deferred:
        resolved = as_day_first if day_first else as_month_first if month_first else None
        if resolved is None:
            message.reason = "ambiguous_date_order"
            continue
        message.occurred_on = resolved


def _calendar_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _sender_label(rest: str) -> tuple[str | None, str | None]:
    """Return the cleaned sender label, never any text following the sender separator."""
    match = _SENDER_RE.match(rest)
    if match is None:
        # A system notice or a body line without a sender separator carries no identity.
        return None, "no_sender"
    label = _strip_marks(match.group("sender")).strip()
    label = " ".join(label.split())
    if not label:
        return None, "no_sender"
    if len(label) > _MAX_SENDER_LENGTH:
        return None, "invalid_sender"
    return label, None


def _self_identities(
    self_addresses: set[str],
    self_names: set[str] | None,
    self_sender: str | None,
) -> set[str]:
    values = {*self_addresses, *(self_names or set())}
    if self_sender is not None:
        values.add(self_sender)
    return {_identity_key(value) for value in values if value.strip()}


def _identity_key(label: str) -> str:
    """Return the comparison key for a sender label.

    A phone identity is keyed on digits alone, so a label and a self hint that differ only in
    spacing, punctuation, or a leading ``+`` resolve to the same person.
    """
    digits = _phone_digits(label)
    return f"phone:{digits}" if digits is not None else f"name:{normalize_name(label)}"


def _phone_digits(label: str) -> str | None:
    """Return the digits of a phone-number sender label, or ``None`` for a display name."""
    candidate = "".join(" " if char in _NARROW_SPACES else char for char in label).strip()
    if not _PHONE_RE.fullmatch(candidate):
        return None
    digits = "".join(char for char in candidate if char.isdigit())
    return digits if len(digits) >= _MIN_PHONE_DIGITS else None


def _phone_handle(label: str) -> str | None:
    """Return the compact staged form of a phone-number label, preserving its leading ``+``."""
    digits = _phone_digits(label)
    if digits is None:
        return None
    return f"+{digits}" if label.strip().startswith("+") else digits


def _person_candidate(person: _PersonAccumulator) -> dict[str, object]:
    aliases: list[dict[str, str]] = []
    if person.handle is not None:
        aliases.append({"value": person.handle, "kind": AliasKind.HANDLE.value})
    return {
        "type": "person",
        "ref": person.ref,
        "name": person.name,
        "aliases": aliases,
        "message_id": None,
        "date": None,
    }


def _strip_marks(value: str) -> str:
    return "".join(
        " " if char in _NARROW_SPACES else char for char in value if char not in _BIDI_MARKS
    )
