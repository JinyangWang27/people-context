"""The streaming reader and the parser-work budget the converted extractors share."""

from __future__ import annotations

import inspect
import io
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from people_context.adapters.importers.bounded_source import (
    PARSER_WORK_EXHAUSTED,
    SOURCE_TOO_LARGE,
    UNDECODABLE_SOURCE,
    MeteredSourceFile,
    ParserWorkBudget,
    SourceReadBudget,
    iter_split_lines,
    open_source_stream,
    read_source_text,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.ics import IcsImportExtractor
from people_context.adapters.importers.linkedin import LinkedInImportExtractor, _split_lines_lazily
from people_context.adapters.importers.outlook import OutlookImportExtractor
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.importers.vcard import VCardImportExtractor
from people_context.app.imports import MAX_CLI_RETAINED_PARSE_RECORDS, MAX_CLI_SOURCE_BYTES

_LINKEDIN_HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
_OUTLOOK_HEADER = "First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Birthday\n"


def _sparse_file(path: Path, size: int) -> Path:
    """Create a file of exactly ``size`` zero bytes without spending that much disk."""
    path.touch()
    os.truncate(path, size)
    return path


class _RecordingBuffer:
    """A byte stream that records the size each read was actually asked for."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)
        self.requested: list[tuple[str, int]] = []

    def read(self, size: int = -1) -> bytes:
        self.requested.append(("read", size))
        return self._stream.read(size)

    def read1(self, size: int = -1) -> bytes:
        self.requested.append(("read1", size))
        return self._stream.read(size)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._stream.tell()

    def flush(self) -> None:
        """Absorb the flush `TextIOWrapper` issues when it is finalized.

        The wrapper is read-only here, but closing one — including at garbage
        collection — still reaches for `flush` on the object underneath. Without
        it the call surfaces as an unraisable `AttributeError` from the metering
        proxy long after the assertions have passed.
        """

    def close(self) -> None:
        self._stream.close()

    @property
    def closed(self) -> bool:
        return self._stream.closed


# --- the parser-work budget -------------------------------------------------------------


def test_a_parser_work_budget_refuses_past_the_live_record_ceiling() -> None:
    budget = ParserWorkBudget(4)
    budget.account(4)

    with pytest.raises(ImportExtractionError) as refusal:
        budget.account(5)

    assert refusal.value.code == PARSER_WORK_EXHAUSTED
    assert "4" in str(refusal.value)


def test_an_absent_parser_work_budget_never_refuses() -> None:
    ParserWorkBudget(None).account(MAX_CLI_RETAINED_PARSE_RECORDS * 100)


def test_the_cli_parser_work_backstop_cannot_be_reached_inside_the_byte_ceiling() -> None:
    """A record costs at least a byte, so the byte ceiling is the tighter of the two."""
    assert MAX_CLI_RETAINED_PARSE_RECORDS >= MAX_CLI_SOURCE_BYTES


# --- retention: skips stay free ---------------------------------------------------------


def test_a_candidate_free_vcard_source_retains_one_card_not_one_per_card() -> None:
    """Twenty thousand skipped cards must cost what one card costs."""
    source = "BEGIN:VCARD\nNOTE:ignored\nEND:VCARD\n" * 20_000

    extracted = VCardImportExtractor().extract(
        "vcard",
        content=source,
        path=None,
        self_addresses=set(),
        max_retained_parse_records=2,
    )

    assert extracted.candidates == []
    assert len(extracted.skipped_cards) == 20_000


def test_one_unbounded_vcard_record_is_refused_by_the_parser_work_budget() -> None:
    """The budget bounds one record's size, which is the retention streaming cannot remove."""
    source = "BEGIN:VCARD\n" + ("NOTE:ignored\n" * 5_000) + "END:VCARD\n"

    with pytest.raises(ImportExtractionError) as refusal:
        VCardImportExtractor().extract(
            "vcard",
            content=source,
            path=None,
            self_addresses=set(),
            max_retained_parse_records=64,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED


def test_a_candidate_free_ics_source_retains_one_event_not_one_per_event() -> None:
    source = "BEGIN:VCALENDAR\n" + ("BEGIN:VEVENT\nUID:x\nEND:VEVENT\n" * 20_000) + "END:VCALENDAR\n"

    extracted = IcsImportExtractor().extract(
        "ics",
        content=source,
        path=None,
        self_addresses=set(),
        max_retained_parse_records=2,
    )

    assert extracted.candidates == []
    assert len(extracted.skipped_cards) == 20_000


def test_one_event_with_an_unbounded_attendee_fan_out_is_refused() -> None:
    attendees = "".join(f"ATTENDEE:mailto:a{index}@example.com\n" for index in range(5_000))
    source = f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:x\nDTSTART:20240101T090000Z\n{attendees}END:VEVENT\n"

    with pytest.raises(ImportExtractionError) as refusal:
        IcsImportExtractor().extract(
            "ics",
            content=source,
            path=None,
            self_addresses=set(),
            max_retained_parse_records=64,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED


@pytest.mark.parametrize(
    ("extractor", "source_type", "source"),
    [
        (LinkedInImportExtractor(), "linkedin", _LINKEDIN_HEADER + ",,,,,,\n" * 20_000),
        (OutlookImportExtractor(), "outlook", _OUTLOOK_HEADER + ",,,,,,\n" * 20_000),
    ],
)
def test_a_candidate_free_csv_source_retains_one_row(extractor: object, source_type: str, source: str) -> None:
    extracted = extractor.extract(  # type: ignore[attr-defined]
        source_type,
        content=source,
        path=None,
        self_addresses=set(),
        max_retained_parse_records=1,
    )

    assert extracted.candidates == []
    assert len(extracted.skipped_cards) == 20_000


# --- the streaming reader ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "a", "a\n", "a\nb", "a\nb\n", "\n", "\n\n", "one\ntwo\nthree\n"],
)
def test_split_lines_streams_exactly_what_split_returns(text: str) -> None:
    """The line-oriented extractors index by position in this split, trailing empty included."""
    with io.StringIO(text, newline=None) as stream:
        assert list(iter_split_lines(stream)) == text.split("\n")


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        (b"BEGIN\r\nEND\r\n", "utf-8"),
        (b"BEGIN\nEND\n", "utf-8"),
        (b"BEGIN\rEND\r", "utf-8"),
        (b"\xef\xbb\xbfFirst,Last\r\nA,B\r\n", "utf-8-sig"),
        ("naïve — ok\n".encode(), "utf-8"),
        (b"", "utf-8"),
        (b"no trailing newline", "utf-8"),
    ],
)
def test_a_streamed_path_yields_exactly_the_lines_of_the_whole_read(
    raw: bytes,
    encoding: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.write_bytes(raw)
    whole = read_source_text(str(source), encoding=encoding, max_bytes=MAX_CLI_SOURCE_BYTES)

    with open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding=encoding,
        max_bytes=MAX_CLI_SOURCE_BYTES,
        source_label="test",
        universal_newlines=True,
    ) as lines:
        streamed = list(lines)

    assert "".join(streamed) == whole
    assert streamed == list(io.StringIO(whole))


def test_a_streamed_byte_snapshot_matches_the_streamed_path(tmp_path: Path) -> None:
    raw = b"BEGIN\r\nmiddle\r\nEND\r\n"
    source = tmp_path / "snapshot.vcf"
    source.write_bytes(raw)

    with open_source_stream(
        content=None,
        content_bytes=raw,
        path=None,
        encoding="utf-8",
        max_bytes=None,
        source_label="test",
        universal_newlines=True,
    ) as from_bytes:
        by_bytes = list(from_bytes)
    with open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding="utf-8",
        max_bytes=None,
        source_label="test",
        universal_newlines=True,
    ) as from_path:
        by_path = list(from_path)

    assert by_bytes == by_path


def test_a_streamed_content_string_keeps_its_carriage_returns_when_the_caller_splits_on_newlines() -> None:
    """The CSV sources feed `csv` a stream that splits on ``\\n`` alone, exactly as they do today."""
    with open_source_stream(
        content='a,"x\ry",b\n',
        content_bytes=None,
        path=None,
        encoding="utf-8",
        max_bytes=None,
        source_label="test",
        universal_newlines=False,
    ) as lines:
        assert list(lines) == list(io.StringIO('a,"x\ry",b\n'))


def test_a_streamed_content_string_translates_newlines_when_the_caller_asked_for_it() -> None:
    with open_source_stream(
        content="a\r\nb\rc\n",
        content_bytes=None,
        path=None,
        encoding="utf-8",
        max_bytes=None,
        source_label="test",
        universal_newlines=True,
    ) as lines:
        assert list(lines) == ["a\n", "b\n", "c\n"]


def test_a_streamed_content_string_still_loses_its_byte_order_mark() -> None:
    with open_source_stream(
        content="﻿First,Last\n",
        content_bytes=None,
        path=None,
        encoding="utf-8-sig",
        max_bytes=None,
        source_label="test",
        strip_content_bom=True,
        universal_newlines=False,
    ) as lines:
        assert list(lines) == ["First,Last\n"]


def test_the_streaming_reader_still_requires_exactly_one_input() -> None:
    with pytest.raises(ImportExtractionError) as refusal, open_source_stream(
        content="a",
        content_bytes=None,
        path="b",
        encoding="utf-8",
        max_bytes=None,
        source_label="vcard",
        universal_newlines=True,
    ) as lines:
        list(lines)

    assert refusal.value.code == "invalid_source"


# --- decoding ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [0, 1, 8_190, 8_191, 8_192, 8_193, 16_384, 40_000])
def test_an_undecodable_byte_refuses_wherever_the_reader_chunked(offset: int, tmp_path: Path) -> None:
    """The refusal follows the offending byte, not the boundary a read happened to land on."""
    source = tmp_path / "latin1.vcf"
    source.write_bytes(b"a" * offset + b"\xe9" + b"b" * 4_096)

    with pytest.raises(ImportExtractionError) as refusal, open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding="utf-8",
        max_bytes=None,
        source_label="vcard",
        universal_newlines=True,
    ) as lines:
        list(lines)

    assert refusal.value.code == UNDECODABLE_SOURCE
    # No partial line, chunk, or decode position may reach a diagnostic.
    assert str(offset) not in str(refusal.value)
    assert "0xe9" not in str(refusal.value)


@pytest.mark.parametrize("offset", [8_190, 8_191, 8_192, 8_193])
def test_a_multi_byte_sequence_split_across_a_chunk_still_decodes(offset: int, tmp_path: Path) -> None:
    source = tmp_path / "wide.vcf"
    source.write_bytes(b"a" * offset + "é😀".encode() + b"\n")
    expected = read_source_text(str(source), encoding="utf-8", max_bytes=None)

    with open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding="utf-8",
        max_bytes=None,
        source_label="vcard",
        universal_newlines=True,
    ) as lines:
        assert "".join(lines) == expected


def test_an_undecodable_source_refuses_the_same_way_through_a_converted_extractor(tmp_path: Path) -> None:
    source = tmp_path / "latin1.vcf"
    source.write_bytes("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Café Owner\r\nEND:VCARD\r\n".encode("latin-1"))

    with pytest.raises(ImportExtractionError) as refusal:
        VCardImportExtractor().extract("vcard", content=None, path=str(source), self_addresses=set())

    assert refusal.value.code == UNDECODABLE_SOURCE


# --- the byte budget stays in front -----------------------------------------------------


def test_a_streamed_path_past_the_byte_ceiling_is_refused_for_being_oversized(tmp_path: Path) -> None:
    """An oversized source is refused as oversized, not as whatever a partial parse hits first."""
    source = _sparse_file(tmp_path / "over.vcf", MAX_CLI_SOURCE_BYTES + 1)

    with pytest.raises(ImportExtractionError) as refusal, open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding="utf-8",
        max_bytes=MAX_CLI_SOURCE_BYTES,
        source_label="vcard",
        universal_newlines=True,
    ) as lines:
        list(lines)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_a_streamed_path_that_outgrows_its_budget_while_read_is_still_refused(tmp_path: Path) -> None:
    """`stat` is the cheap first answer; the metered read is what actually bounds the stream."""
    source = tmp_path / "grown.vcf"
    source.write_bytes(b"line\n" * 64)

    with pytest.raises(ImportExtractionError) as refusal, open_source_stream(
        content=None,
        content_bytes=None,
        path=str(source),
        encoding="utf-8",
        max_bytes=320,
        source_label="vcard",
        universal_newlines=True,
    ) as lines:
        for _ in lines:
            source.write_bytes(b"line\n" * 4_096)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_a_metered_file_meters_the_read_a_text_wrapper_actually_prefers() -> None:
    """Reading a wrapper line by line reaches for `read1`, which delegation would leave unmetered."""
    inner = _RecordingBuffer(b"x" * 65_536)
    metered = MeteredSourceFile(inner, SourceReadBudget(16))

    with pytest.raises(ImportExtractionError) as refusal:
        list(io.TextIOWrapper(metered, encoding="utf-8", newline=None))

    assert refusal.value.code == SOURCE_TOO_LARGE
    # `read1`, not `read`: naming the method the wrapper reached for is the regression.
    assert inner.requested == [("read1", 17)]


def test_a_metered_read1_falls_back_when_the_source_has_none() -> None:
    class _Plain:
        def __init__(self, data: bytes) -> None:
            self._stream = io.BytesIO(data)

        def read(self, size: int = -1) -> bytes:
            return self._stream.read(size)

        def tell(self) -> int:
            return self._stream.tell()

    metered = MeteredSourceFile(_Plain(b"abcdef"), SourceReadBudget(None))  # type: ignore[arg-type]

    assert metered.read1(3) == b"abc"


# --- the bounded canonical-header scan ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "",
        "plain",
        "plain\n",
        "a\r\nb",
        "a\rb",
        "a\vb\fc",
        "a\x1cb\x1dc\x1ed",
        "a\x85b",
        "a b c",
        "trailing\r\n",
        "\r\n\r\n",
        "\n\r",
        "mixed\r\nand\rmore\nand\fmore",
        "Notes:\x0cFirst Name,Last Name\n",
    ],
)
def test_the_lazy_piece_scan_matches_splitlines_exactly(line: str) -> None:
    """The scan has to break where `str.splitlines` breaks, or it would search a different file."""
    pieces = list(_split_lines_lazily(line))

    assert [piece for _, piece in pieces] == line.splitlines(keepends=True)
    # The offsets are what let the caller slice the tail instead of rejoining a list.
    assert all(line[offset:] == "".join(rest for _, rest in pieces[index:]) for index, (offset, _) in enumerate(pieces))


def test_the_piece_scan_is_lazy_rather_than_a_materialized_split() -> None:
    """A form-feed-heavy line must not become one string object per separator.

    `str.splitlines` on a single line near the byte ceiling would allocate tens of millions of
    pieces — more than the source itself costs — which is exactly the retention the streaming
    scan exists to avoid. Pulling one piece from a line with a million boundaries proves the
    scan never builds them all.
    """
    line = "\x0c".join("x" * 3 for _ in range(1_000_000))
    scan = _split_lines_lazily(line)

    assert inspect.isgenerator(scan)
    assert next(scan) == (0, "xxx\x0c")


def test_a_form_feed_preamble_still_finds_the_canonical_header() -> None:
    source = "Notes:\x0c" + _LINKEDIN_HEADER + "Ada,Lovelace,u,ada@example.com,Corp,Eng,01 Feb 2024\n"

    extracted = LinkedInImportExtractor().extract(
        "linkedin",
        content=source,
        path=None,
        self_addresses=set(),
        max_retained_parse_records=1,
    )

    assert [candidate["type"] for candidate in extracted.candidates] == ["person", "affiliation", "fact"]


# --- refusal precedence: a read or decode failure outranks a parse failure ----------------


def _undecodable_tail(head: bytes) -> bytes:
    """Return a source whose parse objection comes first and whose bad byte comes much later."""
    return head + b"padding,,,,,,\n" * 4_000 + b"\xff\n"


@pytest.mark.parametrize("route", ["content_bytes", "path"])
@pytest.mark.parametrize(
    ("extractor", "source_type", "head"),
    [
        (
            OutlookImportExtractor(),
            "outlook",
            _OUTLOOK_HEADER.encode() + b'"a"oops,,X,x@example.com,,,\n',
        ),
        (
            LinkedInImportExtractor(),
            "linkedin",
            _LINKEDIN_HEADER.encode() + b'"a"oops,u,x@example.com,,,\n',
        ),
        (OutlookImportExtractor(), "outlook", b"wrong,headers,entirely\n"),
    ],
    ids=["outlook-malformed-row", "linkedin-malformed-row", "outlook-wrong-headers"],
)
def test_a_csv_objection_never_outranks_the_undecodable_bytes_behind_it(
    extractor: object,
    source_type: str,
    head: bytes,
    route: str,
    tmp_path: Path,
) -> None:
    """The whole-file read decoded everything before parsing, so decoding still wins.

    Streaming reaches the parser's own objection first and would report `invalid_csv` or
    `invalid_headers` where the released path reported `undecodable_source`. Draining the rest
    of the source on the refusal path restores that order without restoring the whole-file read.
    """
    raw = _undecodable_tail(head)
    if route == "path":
        source = tmp_path / f"{source_type}.csv"
        source.write_bytes(raw)
        inputs = {"content": None, "content_bytes": None, "path": str(source)}
    else:
        inputs = {"content": None, "content_bytes": raw, "path": None}

    with pytest.raises(ImportExtractionError) as refusal:
        extractor.extract(source_type, self_addresses=set(), **inputs)  # type: ignore[attr-defined]

    assert refusal.value.code == UNDECODABLE_SOURCE


@pytest.mark.parametrize(
    ("extractor", "source_type", "head"),
    [
        (VCardImportExtractor(), "vcard", b"BEGIN:VCARD\nVERSION:3.0\nFN:Ada\nEND:VCARD\n" * 2_000),
        (
            IcsImportExtractor(),
            "ics",
            b"BEGIN:VEVENT\nDTSTART:20240101T090000Z\nATTENDEE:mailto:a@example.com\nEND:VEVENT\n" * 2_000,
        ),
    ],
    ids=["vcard", "ics"],
)
def test_a_candidate_ceiling_never_outranks_the_undecodable_bytes_behind_it(
    extractor: object,
    source_type: str,
    head: bytes,
) -> None:
    """Same precedence for the line-oriented sources, whose objection is the candidate ceiling.

    The head is deliberately far larger than one decode chunk, so the parser really does raise
    before the reader has seen the bad byte. A short source would decode entirely on the first
    read and pass whether or not the refusal path drains.
    """
    with pytest.raises(ImportExtractionError) as refusal:
        extractor.extract(  # type: ignore[attr-defined]
            source_type,
            content=None,
            path=None,
            content_bytes=head + b"\xff\n",
            self_addresses=set(),
            max_candidates=1,
        )

    assert refusal.value.code == UNDECODABLE_SOURCE


# --- the open-component stack is retained parse state too ---------------------------------


def test_an_ics_source_of_unclosed_components_is_bounded_by_the_work_budget() -> None:
    """A `BEGIN` that never closes grows the component stack once per line and stages nothing.

    Metering only the current event's attendees would leave exactly the candidate-free shape
    this budget exists to bound growing with the file, so the stack is counted alongside them.
    """
    source = "BEGIN:VTIMEZONE\n" * 20_000

    with pytest.raises(ImportExtractionError) as refusal:
        IcsImportExtractor().extract(
            "ics",
            content=source,
            path=None,
            self_addresses=set(),
            max_retained_parse_records=64,
        )

    assert refusal.value.code == PARSER_WORK_EXHAUSTED


def test_ordinary_ics_nesting_stays_far_inside_the_work_budget() -> None:
    """Real calendars nest a handful deep, so the stack accounting must not refuse them."""
    event = (
        "BEGIN:VEVENT\nUID:u\nDTSTART:20240301T090000Z\nATTENDEE:mailto:a@example.com\n"
        "BEGIN:VALARM\nTRIGGER:-PT5M\nEND:VALARM\nEND:VEVENT\n"
    )
    source = "BEGIN:VCALENDAR\n" + event * 2_000 + "END:VCALENDAR\n"

    extracted = IcsImportExtractor().extract(
        "ics",
        content=source,
        path=None,
        self_addresses=set(),
        max_retained_parse_records=8,
    )

    # One attendee across every event: one person candidate, one interaction per event.
    assert [candidate["type"] for candidate in extracted.candidates] == ["person"] + ["interaction"] * 2_000


# --- sources that cannot report an offset -------------------------------------------------


@pytest.fixture
def fifo_source(tmp_path: Path) -> Callable[[str, bytes], str]:
    """Serve a source over a named pipe: a real path whose stream cannot seek or tell."""

    def make(name: str, raw: bytes) -> str:
        path = tmp_path / name
        os.mkfifo(path)

        def write() -> None:
            try:
                with path.open("wb") as handle:
                    handle.write(raw)
            except BrokenPipeError:
                # A refused read closes the pipe first; that is the case under test.
                pass

        threading.Thread(target=write, daemon=True).start()
        return str(path)

    return make


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX-only")
@pytest.mark.parametrize(
    ("source_type", "raw", "expected"),
    [
        (
            "vcard",
            b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n",
            ["person"],
        ),
        (
            "ics",
            b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u\r\nDTSTART:20240301T090000Z\r\n"
            b"ATTENDEE:mailto:a@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
            ["person", "interaction"],
        ),
        (
            "linkedin",
            b"First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
            b"Ada,Lovelace,u,ada@example.com,Corp,Eng,01 Feb 2024\n",
            ["person", "affiliation", "fact"],
        ),
        (
            "outlook",
            b"First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Birthday\n"
            b"Ada,,Lovelace,ada@example.com,Corp,Eng,1815-12-10\n",
            ["person", "affiliation", "fact"],
        ),
    ],
)
def test_a_source_path_that_cannot_seek_is_still_read(
    source_type: str,
    raw: bytes,
    expected: list[str],
    fifo_source: Callable[[str, bytes], str],
) -> None:
    """`tell()` on a pipe raises rather than answering, and the whole-file reader never asked.

    A named pipe, a process substitution, or `/dev/stdin` is a legitimate path for a format
    whose reader only moves forward, so metering has to count bytes where it cannot ask offsets.
    """
    path = fifo_source(f"{source_type}.pipe", raw)

    extracted = ImportExtractorRouter().extract(source_type, content=None, path=path, self_addresses=set())

    assert [candidate["type"] for candidate in extracted.candidates] == expected


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are POSIX-only")
def test_the_byte_budget_still_bounds_a_source_that_cannot_seek(
    fifo_source: Callable[[str, bytes], str],
) -> None:
    """Counting is equivalent to offsets here: a stream that cannot seek cannot re-read."""
    raw = b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada\r\nEND:VCARD\r\n" * 500
    path = fifo_source("over.pipe", raw)

    with pytest.raises(ImportExtractionError) as refusal:
        VCardImportExtractor().extract(
            "vcard",
            content=None,
            path=path,
            self_addresses=set(),
            max_source_bytes=100,
        )

    assert refusal.value.code == SOURCE_TOO_LARGE
