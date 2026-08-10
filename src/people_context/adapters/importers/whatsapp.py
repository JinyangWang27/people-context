"""WhatsApp plaintext chat-export extraction into narrow staged candidates.

Only the timestamp prefix and the sender label of each exported message are read. Everything
after the sender separator is message body: it is never copied into a candidate, a skip reason,
a log record, or an error, and it is discarded with the rest of the parsed text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{2}|\d{4})$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?(?: ?[APap]\.?[Mm]\.?)?$")
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


@dataclass
class _PersonAccumulator:
    """Batch-local participant deduplicated by normalized sender identity."""

    ref: str
    name: str
    handle: str | None
    alternates: list[str] = field(default_factory=list)


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
            else:
                _add_alternate_name(person, label)
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
            time_token = _strip_marks(match.group("time")).strip()
            if not _TIME_RE.fullmatch(time_token):
                continue
            if not (_ISO_DATE_RE.fullmatch(date_token) or _NUMERIC_DATE_RE.fullmatch(date_token)):
                continue
            messages.append(_Message(index=len(messages) + 1, date_token=date_token, rest=match.group("rest")))
            break
    return messages


def _resolve_dates(messages: list[_Message]) -> None:
    """Assign each message a calendar day, or a stable reason why it has none.

    A WhatsApp export carries no UTC offset, so only the calendar day is meaningful. Numeric
    day/month ordering is locale dependent and is inferred from the whole file rather than
    guessed per line; an export that cannot be resolved unambiguously is skipped.
    """
    deferred: list[tuple[_Message, int, int, int]] = []
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
        first, second, year_token = (int(part) for part in numeric.groups())
        if not _plausible_components(first, second):
            # An impossible component pair is rejected before it can bias the file-wide ordering.
            message.reason = "invalid_timestamp"
            continue
        day_first_evidence = day_first_evidence or first > 12
        month_first_evidence = month_first_evidence or second > 12
        deferred.append((message, first, second, year_token))

    day_first = day_first_evidence and not month_first_evidence
    month_first = month_first_evidence and not day_first_evidence
    for message, first, second, year_token in deferred:
        if not day_first and not month_first:
            message.reason = "ambiguous_date_order"
            continue
        day, month = (first, second) if day_first else (second, first)
        year = year_token + 2000 if year_token < 100 else year_token
        message.occurred_on = _calendar_date(year, month, day)
        if message.occurred_on is None:
            message.reason = "invalid_timestamp"


def _plausible_components(first: int, second: int) -> bool:
    """Return whether the pair can still be a day and a month in either locale order."""
    if not (1 <= first <= 31 and 1 <= second <= 31):
        return False
    return first <= 12 or second <= 12


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
    handle = _phone_handle(label)
    return f"phone:{handle}" if handle is not None else f"name:{normalize_name(label)}"


def _phone_handle(label: str) -> str | None:
    """Return the compact form of a phone-number sender label, or ``None`` for a display name."""
    candidate = "".join(" " if char in _NARROW_SPACES else char for char in label).strip()
    if not _PHONE_RE.fullmatch(candidate):
        return None
    digits = "".join(char for char in candidate if char.isdigit())
    if len(digits) < _MIN_PHONE_DIGITS:
        return None
    return f"+{digits}" if candidate.startswith("+") else digits


def _add_alternate_name(person: _PersonAccumulator, label: str) -> None:
    """Record a differing display label for the same identity, never duplicating a staged value."""
    normalized = normalize_name(label)
    known = {normalize_name(person.name), *(normalize_name(value) for value in person.alternates)}
    if person.handle is not None:
        known.add(normalize_name(person.handle))
    if normalized not in known:
        person.alternates.append(label)


def _person_candidate(person: _PersonAccumulator) -> dict[str, object]:
    aliases: list[dict[str, str]] = []
    if person.handle is not None:
        aliases.append({"value": person.handle, "kind": AliasKind.HANDLE.value})
    aliases.extend({"value": name, "kind": AliasKind.OTHER.value} for name in person.alternates)
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
