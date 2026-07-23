"""Stdlib-only iCalendar (RFC 5545) attendee extraction into narrow staged candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from people_context.adapters.importers.email import ImportExtractionError
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractedImport

# One neutral, source-independent interaction summary. Free text such as SUMMARY,
# DESCRIPTION, LOCATION, or conference URLs is deliberately never retained.
_EVENT_SUMMARY = "Calendar event"
_DATE_RE = re.compile(r"^\d{8}$")
_DATETIME_RE = re.compile(r"^\d{8}T\d{6}$")


@dataclass
class _Event:
    """Mutable per-``VEVENT`` accumulator built while scanning unfolded lines."""

    uid: str | None = None
    dtstart_present: bool = False
    dtstart_params: dict[str, str] = field(default_factory=dict)
    dtstart_value: str = ""
    attendees: list[str] = field(default_factory=list)
    attendee_names: dict[str, str | None] = field(default_factory=dict)
    malformed: bool = False


@dataclass
class _PersonAccumulator:
    """Batch-local person candidate deduplicated by normalized email."""

    ref: str
    name: str
    alternates: list[str] = field(default_factory=list)


class IcsImportExtractor:
    """Parse each ``VEVENT`` independently and retain only attendee identities and a start time."""

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
    ) -> ExtractedImport:
        if source_type != "ics":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'ics'")
        if (content is None) == (path is None):
            raise ImportExtractionError("invalid_source", "ics import requires exactly one of content or path")
        text = content if content is not None else Path(path or "").read_text(encoding="utf-8")
        normalized_self = {normalize_name(address) for address in self_addresses if address.strip()}

        people: dict[str, _PersonAccumulator] = {}
        interactions: list[dict[str, object]] = []
        skipped: list[dict[str, int | str]] = []

        for index, event in enumerate(_iter_events(_unfold_lines(text)), start=1):
            if event.malformed:
                skipped.append({"index": index, "reason": "malformed_event"})
                continue
            if not event.dtstart_present:
                skipped.append({"index": index, "reason": "missing_dtstart"})
                continue
            occurred_at, reason = _parse_dtstart(event.dtstart_params, event.dtstart_value)
            if occurred_at is None:
                skipped.append({"index": index, "reason": reason or "invalid_dtstart"})
                continue
            refs = self._collect_attendees(event, normalized_self, people)
            if not refs:
                skipped.append({"index": index, "reason": "no_external_attendee"})
                continue
            interaction: dict[str, object] = {
                "type": "interaction",
                "summary": _EVENT_SUMMARY,
                "participant_refs": refs,
                "date": occurred_at,
                "channel": "calendar",
                "message_id": event.uid,
            }
            interactions.append(interaction)

        person_candidates = [_person_candidate(accumulator) for accumulator in people.values()]
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[*person_candidates, *interactions],
            skipped_cards=skipped,
        )

    @staticmethod
    def _collect_attendees(
        event: _Event,
        normalized_self: set[str],
        people: dict[str, _PersonAccumulator],
    ) -> list[str]:
        refs: list[str] = []
        for address in event.attendees:
            normalized = normalize_name(address)
            if not normalized or "@" not in normalized or normalized in normalized_self:
                continue
            display = _clean_text(event.attendee_names.get(address) or "") or normalized.split("@", maxsplit=1)[0]
            accumulator = people.get(normalized)
            if accumulator is None:
                people[normalized] = _PersonAccumulator(ref=normalized, name=display)
            elif normalize_name(display) != normalize_name(accumulator.name):
                known = {normalize_name(value) for value in accumulator.alternates}
                if normalize_name(display) not in known:
                    accumulator.alternates.append(display)
            refs.append(normalized)
        return list(dict.fromkeys(refs))


def _person_candidate(accumulator: _PersonAccumulator) -> dict[str, object]:
    aliases: list[dict[str, str]] = [{"value": accumulator.ref, "kind": AliasKind.HANDLE.value}]
    aliases.extend({"value": name, "kind": AliasKind.OTHER.value} for name in accumulator.alternates)
    return {
        "type": "person",
        "ref": accumulator.ref,
        "name": accumulator.name,
        "aliases": aliases,
        "message_id": None,
        "date": None,
    }


def _parse_dtstart(params: dict[str, str], value: str) -> tuple[datetime | None, str | None]:
    """Convert a ``DTSTART`` into a timezone-aware UTC ``datetime`` or a stable skip reason.

    Only the explicitly supported RFC 5545 forms are accepted; the host's local timezone is
    never consulted, and DST-ambiguous or nonexistent wall times are skipped rather than guessed.
    """
    raw = value.strip()
    if not raw:
        return None, "malformed_dtstart"
    if params.get("VALUE", "").upper() == "DATE":
        if not _DATE_RE.match(raw):
            return None, "malformed_dtstart"
        try:
            parsed = datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            return None, "invalid_dtstart"
        return parsed.replace(tzinfo=UTC), None
    tzid = params.get("TZID")
    if raw[-1:] in {"Z", "z"}:
        if tzid is not None:
            return None, "malformed_dtstart"
        core = raw[:-1]
        if not _DATETIME_RE.match(core):
            return None, "malformed_dtstart"
        try:
            parsed = datetime.strptime(core, "%Y%m%dT%H%M%S")
        except ValueError:
            return None, "invalid_dtstart"
        return parsed.replace(tzinfo=UTC), None
    if tzid is not None:
        if not _DATETIME_RE.match(raw):
            return None, "malformed_dtstart"
        try:
            zone = ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
            return None, "unknown_tzid"
        try:
            naive = datetime.strptime(raw, "%Y%m%dT%H%M%S")
        except ValueError:
            return None, "invalid_dtstart"
        first = naive.replace(tzinfo=zone, fold=0)
        second = naive.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            # Two candidate offsets: either a fall-back overlap (ambiguous) or a spring-forward
            # gap (nonexistent). Distinguish by whether the wall time survives a UTC round trip.
            roundtrip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            return None, "ambiguous_dtstart" if roundtrip == naive else "nonexistent_dtstart"
        return first.astimezone(UTC), None
    if not _DATETIME_RE.match(raw):
        return None, "malformed_dtstart"
    return None, "floating_dtstart_unsupported"


def _iter_events(lines: list[str]) -> list[_Event]:
    events: list[_Event] = []
    stack: list[str] = []
    current: _Event | None = None
    for line in lines:
        if not line.strip():
            continue
        try:
            name, params, value = _split_property(line)
        except ValueError:
            if current is not None:
                current.malformed = True
            continue
        upper = name.upper()
        if upper == "BEGIN":
            component = value.strip().upper()
            stack.append(component)
            if component == "VEVENT":
                if current is not None:
                    current.malformed = True
                current = _Event()
                events.append(current)
        elif upper == "END":
            component = value.strip().upper()
            if stack and stack[-1] == component:
                stack.pop()
            else:
                # A mismatched or stray END means the component nesting is malformed;
                # the active event cannot be trusted structurally.
                if current is not None:
                    current.malformed = True
                while component in stack and stack.pop() != component:
                    pass
            if component == "VEVENT":
                current = None
        elif current is not None and stack and stack[-1] == "VEVENT":
            _apply_property(current, upper, params, value)
    if current is not None:
        current.malformed = True
    return events


def _apply_property(event: _Event, name: str, params: dict[str, str], value: str) -> None:
    if name == "DTSTART":
        if not event.dtstart_present:
            event.dtstart_present = True
            event.dtstart_params = params
            event.dtstart_value = value
    elif name == "UID":
        if event.uid is None:
            cleaned = " ".join(value.split())
            event.uid = cleaned or None
    elif name == "ATTENDEE":
        address = _mailto_address(value)
        event.attendees.append(address)
        common_name = params.get("CN")
        if address and address not in event.attendee_names:
            event.attendee_names[address] = common_name


def _mailto_address(value: str) -> str:
    stripped = value.strip()
    if not stripped.lower().startswith("mailto:"):
        return ""
    address = stripped[len("mailto:") :].strip()
    # An RFC 6068 mailto URI may carry ?header=value query fields (e.g. ?subject=...);
    # keep only the address so query text never becomes a handle and self matching holds.
    return address.split("?", maxsplit=1)[0].strip()


def _split_property(line: str) -> tuple[str, dict[str, str], str]:
    colon = _index_unquoted(line, ":")
    if colon == -1:
        raise ValueError("malformed iCalendar property")
    left, value = line[:colon], line[colon + 1 :]
    pieces = _split_unquoted(left, ";")
    name = pieces[0].strip()
    if not name:
        raise ValueError("malformed iCalendar property name")
    params: dict[str, str] = {}
    for item in pieces[1:]:
        if "=" in item:
            key, _, raw = item.partition("=")
            params[key.strip().upper()] = _strip_quotes(raw.strip())
    return name, params, value


def _index_unquoted(text: str, target: str) -> int:
    in_quote = False
    for index, char in enumerate(text):
        if char == '"':
            in_quote = not in_quote
        elif char == target and not in_quote:
            return index
    return -1


def _split_unquoted(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    for char in text:
        if char == '"':
            in_quote = not in_quote
            current.append(char)
        elif char == separator and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _unfold_lines(text: str) -> list[str]:
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in physical:
        if line[:1] in {" ", "\t"} and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded
