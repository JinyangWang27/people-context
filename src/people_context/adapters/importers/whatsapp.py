"""WhatsApp plaintext chat-export extraction into narrow staged candidates.

Only the timestamp prefix and the sender label of each exported message are read. Everything
after the sender separator is message body: it is never copied into a candidate, a skip reason,
a log record, or an error, and it is discarded with the rest of the parsed text.

This source is the one M20 case where streaming and extraction semantics genuinely pull against
each other. Numeric day/month ordering is locale dependent, and M14 resolves it **from the whole
file**: a header that is a real date in exactly one reading is evidence, and an export offering
none or offering both is skipped as a unit. A parser that decided the ordering from a bounded
prefix would extract different candidates from the same file, which this milestone forbids.

The resolution taken here is the spec's preferred one, a **bounded two-pass scan**. The first
pass streams the export and keeps only two booleans — whether any header was day-first evidence
and whether any was month-first evidence — which is the entire input to the ordering decision and
is O(1) in file size. The second pass streams the same export again and emits candidates under
that decision, holding one message at a time. What is extracted is therefore byte for byte what
the whole-file parser extracted, and what is retained no longer grows with the number of messages
or, more importantly, with the number of *skipped* messages, which produce no candidate for the
staging ceiling to meter.

The cost is reading the source twice, and a source read twice can change in between. Each pass
digests exactly the text it decoded, and a pass pair that did not read the same export is refused
rather than allowed to answer with one version's ordering over another version's messages.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from people_context.adapters.importers.bounded_source import (
    SOURCE_CHANGED_DURING_IMPORT,
    CandidateBudget,
    ParserWorkBudget,
    drain_source,
    iter_split_lines,
    open_source_stream,
    read_source_bytes,
    require_one_source_input,
)
from people_context.adapters.importers.errors import ImportExtractionError
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


@dataclass(frozen=True)
class _DateOrder:
    """The whole-file numeric day/month ordering, as the two booleans that decide it.

    This is the complete result of the first pass. A count of how many headers pointed each way
    would say no more than these flags do — one unambiguous header settles a direction and a
    second says nothing new — so the evidence an export offers is O(1) however long it is.
    """

    day_first: bool
    month_first: bool


@dataclass(frozen=True)
class _PersonAccumulator:
    """Batch-local participant deduplicated by normalized sender identity.

    A label that differs from this one is a different identity by construction — a name keys on
    its normalized form and a phone keys on its digits — so there is no alternate-label state.
    """

    ref: str
    name: str
    handle: str | None


class _ChatSource:
    """One chat export, readable twice under the same byte budget and the same decoding rules.

    Both passes go through the shared streaming reader with identical arguments, so the second
    read is bounded exactly as the first was: a source that grew past the caller's byte ceiling
    between them is refused for its size rather than parsed halfway.

    Each pass also digests the text it decoded, which is what lets two reads be compared without
    holding either. The digest deliberately covers what the parser consumed rather than the bytes
    underneath it: a rewrite that only changed line endings or a byte-order mark decodes to the
    same lines and would have produced the same candidates, so refusing it would report a change
    that could not have affected the answer. An in-memory ``content`` or ``content_bytes`` source
    cannot differ between passes at all; it is still digested, so the guarantee is a property of
    the extractor rather than of which input route a caller happened to use.

    Not every path can be read twice, and the ones that cannot are paths this project already
    supports. `MeteredSourceFile` names them: a FIFO, a process substitution, or ``/dev/stdin``
    is a legitimate source for a reader that only moves forward, and the whole-file read this
    replaced consumed one exactly once. Opening such a path a second time does not read it
    again — it blocks for another writer, or sees end of file and looks like a source that
    changed — so a one-shot path is snapshotted into memory before the first pass and both
    passes read the snapshot. That holds the whole source, which is precisely what streaming
    exists to avoid, and it is still the right answer here: it is exactly what the released
    implementation held for exactly these inputs, so no supported import is narrowed, while
    every ordinary file keeps the bound this milestone added.
    """

    def __init__(
        self,
        *,
        content: str | None,
        content_bytes: bytes | None,
        path: str | None,
        max_bytes: int | None,
    ) -> None:
        # Refusing an ill-formed request here rather than on the first read keeps `invalid_source`
        # ahead of anything the snapshot below could raise about a path the caller never meant to
        # be read alone, exactly as the whole-file resolver ordered the two.
        require_one_source_input(
            content=content,
            content_bytes=content_bytes,
            path=path,
            source_label="whatsapp",
        )
        self._content = content
        self._content_bytes = content_bytes
        self._path = path
        self._max_bytes = max_bytes
        self._passes: list[str] = []

    def snapshot_if_read_once(self) -> None:
        """Replace a path that yields its bytes once with the bytes themselves.

        The read is the same bounded read the whole-file implementation performed for every path,
        so a one-shot source over the caller's byte ceiling is still refused as `source_too_large`
        and nothing about which sources are accepted changes. A regular file is left alone: it can
        be read twice, and reading it twice is what keeps this source's retention bounded.
        """
        if self._path is None or _rereadable(self._path):
            return
        self._content_bytes = read_source_bytes(self._path, max_bytes=self._max_bytes)
        self._path = None

    @contextmanager
    def read(self) -> Iterator[Iterator[str]]:
        """Yield one pass over the export's lines, recording what that pass actually decoded.

        The lines are exactly the elements ``text.split("\\n")`` produced for the whole-file
        reader, so every position, index, and trailing-empty-line behaviour the released parser
        depended on is unchanged. Hashing happens before the split, while each line still carries
        its terminator, so two different sources cannot digest alike by losing the boundaries
        between their lines.
        """
        hasher = hashlib.sha256()
        passes = self._passes

        def digested(lines: Iterable[str]) -> Iterator[str]:
            for line in lines:
                hasher.update(line.encode("utf-8"))
                yield line
            # Only a pass that reached the end of its source describes that source, so a pass
            # abandoned by a refusal deliberately records nothing.
            passes.append(hasher.hexdigest())

        with open_source_stream(
            content=self._content,
            content_bytes=self._content_bytes,
            path=self._path,
            encoding="utf-8",
            max_bytes=self._max_bytes,
            source_label="whatsapp",
            universal_newlines=True,
        ) as lines:
            yield iter_split_lines(digested(lines))

    def read_one_export(self) -> bool:
        """Whether both completed passes decoded exactly the same export."""
        return len(self._passes) == 2 and self._passes[0] == self._passes[1]


def _rereadable(path: str) -> bool:
    """Whether opening this path a second time reads the same bytes a second time.

    A regular file does, and a symlink to one does because this follows it. A FIFO, a process
    substitution, a socket, or a character device does not: its bytes are gone once read, so a
    second open blocks for another writer or returns end of file. Two passes over one of those is
    not a slower answer, it is a wrong one.

    A missing path, a directory, or anything else `stat` refuses raises here exactly as the open
    that used to be the first thing this source did would have raised, so the error a caller sees
    for an unreadable path is unchanged.
    """
    return stat.S_ISREG(os.stat(path).st_mode)


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
        content_bytes: bytes | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
        max_retained_parse_records: int | None = None,
    ) -> ExtractedImport:
        """Extract external participants and one neutral interaction per calendar day.

        The export is scanned twice: once for the ordering evidence the whole file carries, and
        once for the candidates that evidence resolves. Neither pass retains more than the
        message it is looking at, so ``max_retained_parse_records`` bounds this source the way it
        bounds the others — including the case the candidate ceiling structurally cannot reach,
        an export whose every message is skipped and which therefore stages nothing at all.
        """
        if source_type != "whatsapp":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'whatsapp'")
        work = ParserWorkBudget(max_retained_parse_records)
        source = _ChatSource(
            content=content,
            content_bytes=content_bytes,
            path=path,
            max_bytes=max_source_bytes,
        )
        # A path that yields its bytes once cannot answer two passes; it becomes a snapshot
        # before either of them runs, so both read the same source and neither blocks.
        source.snapshot_if_read_once()

        with source.read() as lines:
            try:
                order = _scan_date_order(lines, work)
            except ImportExtractionError:
                # A parse refusal must not outrank one the rest of the source would have
                # produced: the whole-file read decoded everything before parsing anything.
                drain_source(lines)
                raise

        with source.read() as lines:
            try:
                extracted = _collect_candidates(
                    lines,
                    self_identities=self_identity_keys(self_addresses, self_names, self_sender),
                    order=order,
                    budget=CandidateBudget(max_candidates),
                    work=work,
                )
            except ImportExtractionError:
                drain_source(lines)
                raise

        if not source.read_one_export():
            raise ImportExtractionError(
                SOURCE_CHANGED_DURING_IMPORT,
                "source changed while it was being imported; nothing was staged",
            )
        return extracted


def _scan_date_order(lines: Iterable[str], work: ParserWorkBudget) -> _DateOrder:
    """Reduce the whole export to the ordering evidence its numeric headers carry.

    Only a token that is a real date in exactly one order says anything about the locale, so an
    ISO header and an impossible one both contribute nothing. The scan reads to the end even once
    both directions have been seen: stopping early would leave the rest of the source unread, and
    the byte ceiling and the decoding refusal that go with it are properties of reading all of it.
    """
    day_first_evidence = False
    month_first_evidence = False
    for date_token, _rest in _iter_messages(lines):
        # One header is live per turn, and the two calendar readings taken from it below are
        # discarded before the next one is read; only the two evidence flags survive the loop.
        # Accounting it puts this pass on the same seam as the second, so a first pass that
        # started accumulating fails a test rather than passing review unnoticed.
        work.account(1)
        numeric = _NUMERIC_DATE_RE.fullmatch(date_token)
        if numeric is None:
            continue
        as_day_first, as_month_first = _numeric_readings(numeric)
        if as_day_first is None and as_month_first is None:
            # Impossible in both orders, so it is neither a usable date nor usable evidence.
            continue
        day_first_evidence = day_first_evidence or as_month_first is None
        month_first_evidence = month_first_evidence or as_day_first is None
    return _DateOrder(
        day_first=day_first_evidence and not month_first_evidence,
        month_first=month_first_evidence and not day_first_evidence,
    )


def _collect_candidates(
    lines: Iterable[str],
    *,
    self_identities: set[str],
    order: _DateOrder,
    budget: CandidateBudget,
    work: ParserWorkBudget,
) -> ExtractedImport:
    """Turn each streamed message into a participant, a day's interaction, or one skip reason."""
    people: list[_PersonAccumulator] = []
    people_by_identity: dict[str, _PersonAccumulator] = {}
    refs_by_day: dict[date, list[str]] = {}
    skipped: list[dict[str, int | str]] = []

    for index, (date_token, rest) in enumerate(_iter_messages(lines), start=1):
        # Exactly one message is live per turn of this loop, and its date token and remainder
        # become unreachable as soon as the names are rebound. Accounting it explicitly is what
        # puts the streamed shape on the budget seam, so a regression back to a materialized
        # message list fails a test rather than passing review unnoticed.
        work.account(1)
        occurred_on, reason = _resolve_date(date_token, order)
        if occurred_on is None:
            skipped.append({"index": index, "reason": reason or "invalid_timestamp"})
            continue
        label, sender_reason = _sender_label(rest)
        if label is None:
            skipped.append({"index": index, "reason": sender_reason or "no_sender"})
            continue
        identity = _identity_key(label)
        if identity in self_identities:
            # Self participation is implicit: no candidate and no participant reference.
            refs_by_day.setdefault(occurred_on, [])
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
        day_refs = refs_by_day.setdefault(occurred_on, [])
        if person.ref not in day_refs:
            day_refs.append(person.ref)
        budget.account(len(people) + len(refs_by_day))

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


def _iter_messages(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield the date token and post-timestamp remainder of each line that starts a message.

    A line that does not carry a complete, well-formed timestamp prefix is a continuation of the
    previous message body and is dropped without being inspected further. Nothing is carried
    between lines, so an export of a million continuations costs what one costs — the property
    the retain-every-message version could not offer.
    """
    for raw_line in lines:
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
            yield date_token, match.group("rest")
            break


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


def _numeric_readings(numeric: re.Match[str]) -> tuple[date | None, date | None]:
    """Return one numeric header's calendar dates under each locale ordering, or ``None`` each.

    A two-digit year is read as ``20YY``. Which of the two readings is real is exactly the
    evidence the first pass collects and the decision the second pass applies, so both passes
    derive them here rather than each spelling the rule out again.
    """
    first, second, year_token = (int(numeric.group(name)) for name in ("first", "second", "year"))
    year = year_token + 2000 if year_token < 100 else year_token
    return _calendar_date(year, second, first), _calendar_date(year, first, second)


def _resolve_date(date_token: str, order: _DateOrder) -> tuple[date | None, str | None]:
    """Assign one message its calendar day, or a stable reason why it has none.

    A WhatsApp export carries no UTC offset, so only the calendar day is meaningful. The numeric
    ordering is the whole file's answer, taken from the first pass, never a per-line guess: an
    export that offers no evidence or contradictory evidence resolves no numeric header at all.
    """
    iso = _ISO_DATE_RE.fullmatch(date_token)
    if iso is not None:
        year, month, day = (int(part) for part in iso.groups())
        occurred_on = _calendar_date(year, month, day)
        return (occurred_on, None) if occurred_on is not None else (None, "invalid_timestamp")
    numeric = _NUMERIC_DATE_RE.fullmatch(date_token)
    if numeric is None:  # pragma: no cover - detection accepts only the two forms above
        return None, "invalid_timestamp"
    as_day_first, as_month_first = _numeric_readings(numeric)
    if as_day_first is None and as_month_first is None:
        return None, "invalid_timestamp"
    resolved = as_day_first if order.day_first else as_month_first if order.month_first else None
    return (resolved, None) if resolved is not None else (None, "ambiguous_date_order")


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


def self_identity_keys(
    self_addresses: set[str],
    self_names: set[str] | None,
    self_sender: str | None,
) -> set[str]:
    """Return the comparison keys this source treats as the user's own sender labels.

    Public because the extraction fingerprint has to be derived from exactly the identities this
    extractor resolves against: a self hint written ``+1 555 0100`` and one written ``15550100``
    change nothing about what is extracted, so they must not split one source into two claims.
    """
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
