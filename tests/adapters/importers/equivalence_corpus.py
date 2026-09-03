"""One fixture corpus every source's parser must reproduce byte for byte.

M20 rewrites how the extractors read their sources and must not change what they extract.
"Must not change" is only checkable against something, so this module holds the fixtures and
the normal form, and `equivalence_golden.json` holds what the pre-streaming implementation
produced for every one of them. A conversion that alters a candidate, its order, its ref, a
skip reason, or a one-based index fails the comparison rather than passing review.

The corpus is table-driven so coverage is by construction: every supported source appears, and
each source's entries deliberately include the malformed, skipped, and boundary shapes whose
handling is easiest to lose when a whole-file parse becomes a streaming one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from people_context.ports.imports import ExtractedImport

#: Sources whose reader owns the file, so only the path route can be exercised.
PATH_ONLY = frozenset({"mbox"})


@dataclass(frozen=True)
class SourceFixture:
    """One source snapshot and the self-identity configuration it is parsed under."""

    id: str
    source_type: str
    raw: bytes
    self_addresses: set[str] = field(default_factory=set)
    self_names: set[str] | None = None
    self_sender: str | None = None
    suffix: str = ".txt"

    @property
    def routes(self) -> tuple[str, ...]:
        """The accepted input routes for this fixture's source."""
        return ("path",) if self.source_type in PATH_ONLY else ("content", "content_bytes", "path")


def _vcard(body: str) -> bytes:
    return body.encode("utf-8")


_VCARD_MINIMAL = _vcard("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEMAIL:ada@example.com\r\nEND:VCARD\r\n")

_VCARD_RICH = _vcard(
    "BEGIN:VCARD\r\n"
    "VERSION:4.0\r\n"
    "FN:Grace Hopper\r\n"
    "N:Hopper;Grace;Brewster;Dr.;PhD\r\n"
    "NICKNAME:Amazing Grace,Grace\r\n"
    "EMAIL:grace@example.com\r\n"
    "EMAIL:g.hopper@example.com\r\n"
    "ORG:US Navy;Research\r\n"
    "TITLE:Rear Admiral\r\n"
    "BDAY:1906-12-09\r\n"
    "NOTE:private note that must never be staged\r\n"
    "TEL:+1-555-0100\r\n"
    "END:VCARD\r\n"
)

_VCARD_FOLDED = _vcard(
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:Katherine\r\n Johnson\r\n"
    "ORG:NASA;Flight\r\n Research\r\n"
    "TITLE:Mathe\r\n matician\r\n"
    "END:VCARD\r\n"
)

_VCARD_QUOTED_PRINTABLE = _vcard(
    "BEGIN:VCARD\n"
    "VERSION:2.1\n"
    "FN;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:Jos=\nC3=A9 Arcadio\n"
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "VERSION:3.0\n"
    "FN;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:Jos=C3=A9 Arcadio\n"
    "END:VCARD\n"
)

_VCARD_MALFORMED = _vcard(
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "VERSION:3.0\n"
    "FN:Unterminated\n"
    "BEGIN:VCARD\n"
    "VERSION:3.0\n"
    "FN:Second Start\n"
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "VERSION:9.9\n"
    "FN:Future Format\n"
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "VERSION:3.0\n"
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "no colon here\n"
    "END:VCARD\n"
    "BEGIN:VCARD\n"
    "VERSION:3.0\n"
    "FN:Never Closed\n"
)

_VCARD_BARE_CR = b"BEGIN:VCARD\rVERSION:3.0\rFN:Carriage Return\rEND:VCARD\r"

_VCARD_NO_TRAILING_NEWLINE = _vcard("BEGIN:VCARD\nVERSION:3.0\nFN:No Trailing Newline\nEND:VCARD")

_VCARD_EMPTY = b""

_ICS_MIXED = (
    b"BEGIN:VCALENDAR\r\n"
    b"VERSION:2.0\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:utc-1\r\n"
    b"DTSTART:20240301T090000Z\r\n"
    b"ATTENDEE;CN=Ada Lovelace:mailto:ada@example.com\r\n"
    b"ATTENDEE;CN=Self:mailto:me@example.com\r\n"
    b"SUMMARY:never staged\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:tz-1\r\n"
    b"DTSTART;TZID=Europe/Amsterdam:20240301T090000\r\n"
    b"ATTENDEE;CN=Ada L.:mailto:ADA@example.com\r\n"
    b"ATTENDEE:mailto:bob@example.com?subject=hi\r\n"
    b"BEGIN:VALARM\r\n"
    b"TRIGGER:-PT15M\r\n"
    b"END:VALARM\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:allday-1\r\n"
    b"DTSTART;VALUE=DATE:20240302\r\n"
    b"ATTENDEE:mailto:cleo@example.com\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:floating-1\r\n"
    b"DTSTART:20240303T090000\r\n"
    b"ATTENDEE:mailto:dora@example.com\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:no-dtstart\r\n"
    b"ATTENDEE:mailto:eve@example.com\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:self-only\r\n"
    b"DTSTART:20240304T090000Z\r\n"
    b"ATTENDEE:mailto:me@example.com\r\n"
    b"END:VEVENT\r\n"
    b"BEGIN:VEVENT\r\n"
    b"UID:unknown-tz\r\n"
    b"DTSTART;TZID=Mars/Olympus:20240305T090000\r\n"
    b"ATTENDEE:mailto:fay@example.com\r\n"
    b"END:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)

_ICS_FOLDED_AND_BROKEN = (
    b"BEGIN:VCALENDAR\n"
    b"BEGIN:VEVENT\n"
    b"UID:folded\n -uid\n"
    b"DTSTART:20240401T\n 120000Z\n"
    b"ATTENDEE;CN=Long\n  Name:mailto:long@example.com\n"
    b"END:VEVENT\n"
    b"END:VEVENT\n"
    b"BEGIN:VEVENT\n"
    b"UID:nested-mismatch\n"
    b"DTSTART:20240402T120000Z\n"
    b"BEGIN:VALARM\n"
    b"END:VTODO\n"
    b"ATTENDEE:mailto:mixed@example.com\n"
    b"END:VEVENT\n"
    b"BEGIN:VEVENT\n"
    b"UID:unterminated\n"
    b"DTSTART:20240403T120000Z\n"
    b"ATTENDEE:mailto:tail@example.com\n"
)

_ICS_AMBIGUOUS = (
    b"BEGIN:VCALENDAR\n"
    b"BEGIN:VEVENT\n"
    b"UID:ambiguous\n"
    b"DTSTART;TZID=Europe/Amsterdam:20241027T023000\n"
    b"ATTENDEE:mailto:amb@example.com\n"
    b"END:VEVENT\n"
    b"BEGIN:VEVENT\n"
    b"UID:nonexistent\n"
    b"DTSTART;TZID=Europe/Amsterdam:20240331T023000\n"
    b"ATTENDEE:mailto:non@example.com\n"
    b"END:VEVENT\n"
    b"BEGIN:VEVENT\n"
    b"UID:bad-date\n"
    b"DTSTART;VALUE=DATE:20240230\n"
    b"ATTENDEE:mailto:bad@example.com\n"
    b"END:VEVENT\n"
    b"END:VCALENDAR\n"
)

# A second `BEGIN:VEVENT` arriving while one is still open is the point at which streaming has
# to emit the abandoned event: it is malformed and nothing later can change that, so the old
# whole-file scan stopped touching it here too. Covering it pins the emission order that keeps
# the one-based skip indexes counting from the same origin.
_ICS_REOPENED_EVENT = (
    b"BEGIN:VCALENDAR\n"
    b"BEGIN:VEVENT\n"
    b"UID:never-closed\n"
    b"DTSTART:20240501T090000Z\n"
    b"ATTENDEE:mailto:one@example.com\n"
    b"BEGIN:VEVENT\n"
    b"UID:reopened\n"
    b"DTSTART:20240502T090000Z\n"
    b"ATTENDEE:mailto:two@example.com\n"
    b"END:VEVENT\n"
    b"END:VCALENDAR\n"
)

_LINKEDIN_HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On"

_LINKEDIN_WITH_PREAMBLE = (
    "Notes:\n"
    '"When exporting your connection data, you may notice..."\n'
    "\n"
    f"{_LINKEDIN_HEADER}\n"
    "Ada,Lovelace,https://example.invalid/ada,ada@example.com,Analytical Engines,Engineer,01 Feb 2024\n"
    "Ada,L.,https://example.invalid/ada2,ADA@example.com,Analytical Engines,Engineer,2024-02-01\n"
    "Grace,Hopper,https://example.invalid/g,,US Navy,Rear Admiral,15 Mar 2020\n"
    "Grace,Hopper,https://example.invalid/g2,,Other Corp,Advisor,15 Mar 2020\n"
    ",,https://example.invalid/anon,anon@example.com,,,\n"
    "Bad,Date,https://example.invalid/bd,bd@example.com,,,32 Xxx 2024\n"
    "Bad,Email,https://example.invalid/be,not-an-email,,,\n"
    "Me,Myself,https://example.invalid/me,me@example.com,,,\n"
).encode("utf-8-sig")

_LINKEDIN_EXOTIC_PREAMBLE = (
    "Notes:\x0c" + _LINKEDIN_HEADER + "\nCleo,Patra,https://example.invalid/c,cleo@example.com,Nile,Ruler,01 Jan 2024\n"
).encode()

_LINKEDIN_NO_HEADER = b"Some,Other,Csv\n1,2,3\n"

_LINKEDIN_QUOTED_MULTILINE = (
    f"{_LINKEDIN_HEADER}\n"
    '"Multi\nLine",Name,https://example.invalid/m,multi@example.com,"Corp, Inc.",Chief,05 May 2021\n'
).encode()

_OUTLOOK_HEADER = "First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Birthday,Home Phone"

_OUTLOOK_CONTACTS = (
    f"{_OUTLOOK_HEADER}\n"
    "Ada,,Lovelace,ada@example.com,Analytical Engines,Engineer,1815-12-10,555-0100\n"
    "Ada,B,Lovelace,ADA@example.com,Analytical Engines,Engineer,1815-12-10,555-0101\n"
    "Grace,,Hopper,grace@example.com,US Navy,Rear Admiral,09/12/1906,\n"
    ",,,nobody@example.com,,,,\n"
    "Bad,,Email,not-an-email,,,,\n"
    "Me,,Myself,me@example.com,,,,\n"
    "Solo,,Contact,,Solo Corp,Founder,2000-02-29,\n"
).encode("utf-8-sig")

_OUTLOOK_MALFORMED = (f'{_OUTLOOK_HEADER}\nUn,"terminated,Quote,q@example.com,,,,\n').encode()

_EMAIL_MESSAGE = (
    b"From: Ada Lovelace <ada@example.com>\n"
    b"To: Me <me@example.com>, Grace Hopper <grace@example.com>\n"
    b"Cc: bob@example.com\n"
    b"Date: Fri, 01 Mar 2024 09:00:00 +0000\n"
    b"Message-ID: <msg-1@example.com>\n"
    b"Subject: never staged\n"
    b"\n"
    b"body that must never be staged\n"
)

_MBOX = (
    b"From ada@example.com Fri Mar  1 09:00:00 2024\n"
    b"From: Ada Lovelace <ada@example.com>\n"
    b"To: me@example.com\n"
    b"Date: Fri, 01 Mar 2024 09:00:00 +0000\n"
    b"Message-ID: <mbox-1@example.com>\n"
    b"\n"
    b"body\n"
    b"\n"
    b"From grace@example.com Sat Mar  2 09:00:00 2024\n"
    b"From: Grace Hopper <grace@example.com>\n"
    b"To: me@example.com\n"
    b"Date: Sat, 02 Mar 2024 09:00:00 +0000\n"
    b"\n"
    b"body\n"
    b"\n"
    b"From nobody@example.com Sun Mar  3 09:00:00 2024\n"
    b"From: me@example.com\n"
    b"To: me@example.com\n"
    b"Message-ID: <mbox-3@example.com>\n"
    b"\n"
    b"body\n"
)

_WHATSAPP_CHAT = (
    b"01/02/2024, 09:00 - Messages are end-to-end encrypted.\n"
    b"01/02/2024, 09:01 - You: hello there\n"
    b"01/02/2024, 09:02 - Ada Lovelace: hi back\n"
    b"13/02/2024, 10:00 - Ada Lovelace: later message\n"
    b"13/02/2024, 10:01 - +1 555 0100: from a number\n"
    b"not a message line at all\n"
)

_WHATSAPP_UNRESOLVABLE = b"01/02/2024, 09:01 - Ada Lovelace: one\n02/03/2024, 09:02 - Ada Lovelace: two\n"

# Ordering evidence appears on the first line and is consumed by a message four lines later,
# which is exactly the whole-file dependency a naive stream would break.
_WHATSAPP_MONTH_FIRST = (
    b"01/31/2024, 08:00 - Ada Lovelace: month-first evidence\n"
    b"02/03/2024, 08:01 - Grace Hopper: resolved as the third of February\n"
    b"2024-04-05, 08:02 - Ada Lovelace: an ISO header is unaffected\n"
    b"31/02/2024, 08:03 - Ada Lovelace: impossible in either order\n"
)

# The bracketed form, with a meridiem clock, an overlong sender, a body line quoting a
# time-shaped value that must stay a continuation, and a bidi-marked phone label.
_WHATSAPP_BRACKETED = (
    b"[13/02/2024, 9:05:07\xe2\x80\xafAM] Ada Lovelace: narrow-space meridiem\n"
    b"[13/02/2024, 11:00 p.m.] " + b"L" * 90 + b": an implausibly long label\n"
    b"[13/02/2024, 99:99] not a message: a quoted body line\n"
    b"[2024-02-14, 00:00] \xe2\x80\x8e+44 20 7946 0958: a number with a leading mark\n"
)

_WHATSAPP_BARE_CR = b"01/02/2024, 09:01 - Ada Lovelace: one\r13/02/2024, 09:02 - Ada Lovelace: two\r"

_WHATSAPP_NO_TRAILING_NEWLINE = b"2024-03-01, 09:00 - Ada Lovelace: no trailing newline"

_WHATSAPP_SELF_ONLY_DAY = (
    b"2024-05-01, 09:00 - You: a day of nothing but my own messages\n"
    b"2024-05-02, 09:01 - Ada Lovelace: a reply the next day\n"
)

CORPUS: tuple[SourceFixture, ...] = (
    SourceFixture("vcard-minimal", "vcard", _VCARD_MINIMAL, suffix=".vcf"),
    SourceFixture("vcard-rich", "vcard", _VCARD_RICH, suffix=".vcf"),
    SourceFixture("vcard-folded", "vcard", _VCARD_FOLDED, suffix=".vcf"),
    SourceFixture("vcard-quoted-printable", "vcard", _VCARD_QUOTED_PRINTABLE, suffix=".vcf"),
    SourceFixture("vcard-malformed", "vcard", _VCARD_MALFORMED, suffix=".vcf"),
    SourceFixture("vcard-bare-cr", "vcard", _VCARD_BARE_CR, suffix=".vcf"),
    SourceFixture("vcard-no-trailing-newline", "vcard", _VCARD_NO_TRAILING_NEWLINE, suffix=".vcf"),
    SourceFixture("vcard-empty", "vcard", _VCARD_EMPTY, suffix=".vcf"),
    SourceFixture("vcard-self-filtered", "vcard", _VCARD_RICH, {"grace@example.com"}, suffix=".vcf"),
    SourceFixture("ics-mixed", "ics", _ICS_MIXED, {"me@example.com"}, suffix=".ics"),
    SourceFixture("ics-folded-and-broken", "ics", _ICS_FOLDED_AND_BROKEN, suffix=".ics"),
    SourceFixture("ics-ambiguous", "ics", _ICS_AMBIGUOUS, suffix=".ics"),
    SourceFixture("ics-reopened-event", "ics", _ICS_REOPENED_EVENT, suffix=".ics"),
    SourceFixture("ics-empty", "ics", b"", suffix=".ics"),
    SourceFixture("linkedin-preamble", "linkedin", _LINKEDIN_WITH_PREAMBLE, {"me@example.com"}, suffix=".csv"),
    SourceFixture("linkedin-exotic-preamble", "linkedin", _LINKEDIN_EXOTIC_PREAMBLE, suffix=".csv"),
    SourceFixture("linkedin-no-header", "linkedin", _LINKEDIN_NO_HEADER, suffix=".csv"),
    SourceFixture("linkedin-quoted-multiline", "linkedin", _LINKEDIN_QUOTED_MULTILINE, suffix=".csv"),
    SourceFixture("outlook-contacts", "outlook", _OUTLOOK_CONTACTS, {"me@example.com"}, suffix=".csv"),
    SourceFixture("outlook-malformed", "outlook", _OUTLOOK_MALFORMED, suffix=".csv"),
    SourceFixture("outlook-no-header", "outlook", b"a,b,c\n1,2,3\n", suffix=".csv"),
    SourceFixture("email-single", "email", _EMAIL_MESSAGE, {"me@example.com"}, suffix=".eml"),
    SourceFixture("mbox-three", "mbox", _MBOX, {"me@example.com"}, suffix=".mbox"),
    SourceFixture("whatsapp-chat", "whatsapp", _WHATSAPP_CHAT, self_sender="You", suffix=".txt"),
    SourceFixture("whatsapp-unresolvable", "whatsapp", _WHATSAPP_UNRESOLVABLE, self_sender="You", suffix=".txt"),
    SourceFixture("whatsapp-month-first", "whatsapp", _WHATSAPP_MONTH_FIRST, self_sender="You", suffix=".txt"),
    SourceFixture("whatsapp-bracketed", "whatsapp", _WHATSAPP_BRACKETED, self_sender="You", suffix=".txt"),
    SourceFixture("whatsapp-bare-cr", "whatsapp", _WHATSAPP_BARE_CR, self_sender="You", suffix=".txt"),
    SourceFixture(
        "whatsapp-no-trailing-newline",
        "whatsapp",
        _WHATSAPP_NO_TRAILING_NEWLINE,
        self_sender="You",
        suffix=".txt",
    ),
    SourceFixture("whatsapp-empty", "whatsapp", b"", self_sender="You", suffix=".txt"),
    SourceFixture("whatsapp-self-only-day", "whatsapp", _WHATSAPP_SELF_ONLY_DAY, self_sender="You", suffix=".txt"),
)


def fixture_inputs(fixture: SourceFixture, route: str, tmp_path: Path) -> dict[str, Any]:
    """Return the extractor keyword arguments that feed one fixture through one input route."""
    if route == "content":
        return {"content": fixture.raw.decode("utf-8"), "content_bytes": None, "path": None}
    if route == "content_bytes":
        return {"content": None, "content_bytes": fixture.raw, "path": None}
    source = tmp_path / f"{fixture.id}{fixture.suffix}"
    source.write_bytes(fixture.raw)
    return {"content": None, "content_bytes": None, "path": str(source)}


def _stable(value: Any) -> Any:
    """Render the value types a candidate may carry in a form that compares exactly."""
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    raise TypeError(f"unserializable extraction value: {type(value)!r}")


def extraction_snapshot(extracted: ExtractedImport) -> Any:
    """Return one extraction as ordered plain data, so a comparison covers order as well as content."""
    payload = {
        "people": [asdict(person) for person in extracted.people],
        "interactions": [asdict(interaction) for interaction in extracted.interactions],
        "skipped_message_ids": list(extracted.skipped_message_ids),
        "skipped_without_id": extracted.skipped_without_id,
        "candidates": extracted.candidates,
        "skipped_cards": extracted.skipped_cards,
    }
    return json.loads(json.dumps(payload, ensure_ascii=False, default=_stable))


def corpus_cases() -> Iterator[tuple[SourceFixture, str]]:
    """Yield every (fixture, input route) pair the corpus covers."""
    for fixture in CORPUS:
        for route in fixture.routes:
            yield fixture, route


GOLDEN_PATH = Path(__file__).with_name("equivalence_golden.json")
