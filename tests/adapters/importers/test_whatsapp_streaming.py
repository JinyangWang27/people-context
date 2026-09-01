"""WhatsApp resolves its export in two bounded passes rather than retaining every message (M20.3).

What extraction *produces* for this source is pinned byte for byte by the equivalence corpus, and
the M14 ordering-inference and skip-reason rules are pinned by `test_whatsapp.py`; neither is
restated here. These tests cover the properties those cannot see: that the export is read exactly
twice, that the first pass keeps only O(1) ordering evidence, that whole-file inference still
reaches a message the evidence appears *after*, that what the parser holds live is bounded by the
budget seam rather than by the number of messages, that a source which changed between the two
reads is refused instead of answered with one version's ordering over another version's messages,
and that a path which can only be read once is still imported rather than read a second time.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.importers import whatsapp as whatsapp_module
from people_context.adapters.importers.bounded_source import (
    PARSER_WORK_EXHAUSTED,
    SOURCE_CHANGED_DURING_IMPORT,
    SOURCE_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    UNDECODABLE_SOURCE,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.whatsapp import WhatsAppImportExtractor

_SELF = "You"

# Every header is a real date in both readings, so the export offers no ordering evidence and
# every message is skipped as `ambiguous_date_order`. It stages nothing at all, which is exactly
# the shape the candidate ceiling structurally cannot meter.
_AMBIGUOUS_MESSAGE = "02/03/2024, 09:00 - Ada Lovelace: a message\n"


def _write(tmp_path: Path, name: str, raw: bytes) -> str:
    source = tmp_path / name
    source.write_bytes(raw)
    return str(source)


def _extract(**kwargs: Any) -> Any:
    """Extract one WhatsApp export, filling in the arguments every case here shares."""
    return WhatsAppImportExtractor().extract(
        "whatsapp",
        self_addresses=set(),
        self_sender=_SELF,
        **{"content": None, "path": None, **kwargs},
    )


def _count_passes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count how many times the extractor opens its source, so passes are observed not inferred."""
    opens = [0]
    real = whatsapp_module.open_source_stream

    @contextmanager
    def _counting(**kwargs: Any) -> Iterator[Iterator[str]]:
        opens[0] += 1
        with real(**kwargs) as lines:
            yield lines

    monkeypatch.setattr(whatsapp_module, "open_source_stream", _counting)
    return opens


def _rewrite_after_first_pass(monkeypatch: pytest.MonkeyPatch, source: str, replacement: bytes) -> None:
    """Replace the file on disk once the first pass has finished reading it."""
    opens = [0]
    real = whatsapp_module.open_source_stream

    @contextmanager
    def _rewriting(**kwargs: Any) -> Iterator[Iterator[str]]:
        opens[0] += 1
        with real(**kwargs) as lines:
            yield lines
        if opens[0] == 1:
            Path(source).write_bytes(replacement)

    monkeypatch.setattr(whatsapp_module, "open_source_stream", _rewriting)


def test_the_export_is_read_exactly_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two passes is the whole cost of preserving whole-file inference; a third would be waste."""
    source = _write(tmp_path, "chat.txt", b"31/01/2024, 09:00 - Ada Lovelace: one\n")
    opens = _count_passes(monkeypatch)

    extracted = _extract(path=source)

    assert opens[0] == 2
    assert [candidate["type"] for candidate in extracted.candidates] == ["person", "interaction"]


def test_ordering_evidence_still_reaches_a_message_that_precedes_it(tmp_path: Path) -> None:
    """This is the property that rules out a bounded prefix, and it is why there are two passes.

    The first message is a real date in both readings and says nothing on its own. What resolves
    it is the header on the line *after* it, which is day-first evidence. A parser that decided
    the ordering from any bounded prefix of the file would have skipped the first message as
    `ambiguous_date_order`, which is an M14 behaviour change this milestone does not make.
    """
    source = _write(
        tmp_path,
        "late-evidence.txt",
        b"02/03/2024, 09:00 - Ada Lovelace: ambiguous on its own\n31/01/2024, 09:01 - Ada Lovelace: evidence\n",
    )

    extracted = _extract(path=source)

    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]
    assert [interaction["date"].date().isoformat() for interaction in interactions] == [
        "2024-01-31",
        "2024-03-02",
    ]
    assert extracted.skipped_cards == []


def test_a_candidate_free_export_retains_a_constant_rather_than_its_message_count(tmp_path: Path) -> None:
    """The candidate ceiling cannot meter this: an unresolvable export stages nothing at all.

    The budget a thousand-fold longer export needs is the budget the short one needs, which is
    the property under test — retaining a `_Message` per detected line would have made it grow
    with the message count. Both are pinned as the *minimum*, so the equality is not two numbers
    that merely both fit.

    The skip report itself still grows, and deliberately: `skipped_cards` is extraction output
    that the released `ImportBatchResult` carries, exactly as it is for the other sources, not an
    intermediate the parser holds on the way to a candidate.
    """
    extractor = WhatsAppImportExtractor()
    peak = 1

    for count in (5, 5000):
        source = _write(tmp_path, f"ambiguous-{count}.txt", (_AMBIGUOUS_MESSAGE * count).encode("utf-8"))

        extracted = extractor.extract(
            "whatsapp",
            content=None,
            path=source,
            self_addresses=set(),
            self_sender=_SELF,
            max_retained_parse_records=peak,
        )

        assert extracted.candidates == []
        assert len(extracted.skipped_cards) == count
        assert {entry["reason"] for entry in extracted.skipped_cards} == {"ambiguous_date_order"}

        with pytest.raises(ImportExtractionError) as refusal:
            extractor.extract(
                "whatsapp",
                content=None,
                path=source,
                self_addresses=set(),
                self_sender=_SELF,
                max_retained_parse_records=peak - 1,
            )

        assert refusal.value.code == PARSER_WORK_EXHAUSTED


def test_the_first_pass_is_itself_accounted_against_the_parser_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budget of zero must be refused by the ordering scan, before the second pass runs at all.

    Without this the bounded result above would be indistinguishable from a first pass that never
    reaches the seam, which is the regression that would let evidence collection grow unnoticed.
    """
    source = _write(tmp_path, "chat.txt", (_AMBIGUOUS_MESSAGE * 3).encode("utf-8"))
    opens = _count_passes(monkeypatch)

    with pytest.raises(ImportExtractionError) as refusal:
        _extract(path=source, max_retained_parse_records=0)

    assert refusal.value.code == PARSER_WORK_EXHAUSTED
    assert opens[0] == 1
    # The refusal names the limit and nothing about the export it rejected.
    assert source not in str(refusal.value)


def test_a_source_that_changed_between_the_passes_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answering here would mix one version's ordering with another version's messages.

    The rewritten export is day-first, while the ordering the first pass resolved came from a
    month-first one. Nothing about the second pass could notice that on its own, which is why the
    two reads are compared rather than trusted.
    """
    source = _write(tmp_path, "moving.txt", b"01/31/2024, 09:00 - Ada Lovelace: month first\n")
    _rewrite_after_first_pass(monkeypatch, source, b"31/01/2024, 09:00 - Ada Lovelace: day first\n")

    with pytest.raises(ImportExtractionError) as refusal:
        _extract(path=source)

    assert refusal.value.code == SOURCE_CHANGED_DURING_IMPORT
    assert source not in str(refusal.value)


def test_a_change_that_could_not_alter_the_answer_is_not_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The comparison is over what the parser consumed, not over the bytes underneath it.

    Rewriting the same export with different line endings decodes to the same lines under the
    universal-newline handling both passes apply, so the candidates are the ones a single pass
    over either version would have produced. Refusing that would report a change that could not
    have affected the answer.
    """
    source = _write(tmp_path, "renewlined.txt", b"31/01/2024, 09:00 - Ada Lovelace: one\n")
    _rewrite_after_first_pass(monkeypatch, source, b"31/01/2024, 09:00 - Ada Lovelace: one\r\n")

    extracted = _extract(path=source)

    assert [candidate["type"] for candidate in extracted.candidates] == ["person", "interaction"]


def test_the_second_pass_re_applies_the_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source that grew past the ceiling between the reads is refused for its size.

    The second read is a real read of a file that may no longer be the one the budget already
    admitted, so it goes through the same bounded reader rather than trusting the first pass's
    verdict.
    """
    small = b"31/01/2024, 09:00 - Ada Lovelace: one\n"
    source = _write(tmp_path, "growing.txt", small)
    _rewrite_after_first_pass(monkeypatch, source, small + b"x" * 4096)

    with pytest.raises(ImportExtractionError) as refusal:
        _extract(path=source, max_source_bytes=len(small))

    assert refusal.value.code == SOURCE_TOO_LARGE
    assert str(len(small)) in str(refusal.value)


def test_an_in_memory_source_is_never_refused_as_changed() -> None:
    """Two passes over one immutable string are the same pass twice, by construction."""
    extracted = _extract(content="31/01/2024, 09:00 - Ada Lovelace: one\n")

    assert [candidate["type"] for candidate in extracted.candidates] == ["person", "interaction"]


def test_a_decoding_refusal_still_outranks_a_parser_refusal(tmp_path: Path) -> None:
    """The whole-file read decoded everything before parsing anything, so it always won.

    Streaming reverses that by construction — the parser reaches its own objection first — and
    draining the rest of the source on the refusal path is what restores the released precedence
    without restoring the whole-file read.
    """
    source = _write(tmp_path, "undecodable.txt", b"31/01/2024, 09:00 - Ada Lovelace: one\n\xff\xfe not utf-8\n")

    with pytest.raises(ImportExtractionError) as refusal:
        _extract(path=source, max_retained_parse_records=0)

    assert refusal.value.code == UNDECODABLE_SOURCE
    # The refusal names the encoding it expected and no byte, offset, or partial line.
    assert str(refusal.value) == "source is not valid utf-8 text"


def test_a_candidate_ceiling_reached_in_the_second_pass_is_still_refused(tmp_path: Path) -> None:
    """The staging ceiling is unchanged by streaming: it still refuses where it refused before."""
    source = _write(
        tmp_path,
        "crowd.txt",
        b"".join(f"31/01/2024, 09:0{index} - Person {index}: hello\n".encode() for index in range(5)),
    )

    with pytest.raises(ImportExtractionError) as refusal:
        _extract(path=source, max_candidates=2)

    assert refusal.value.code == TOO_MANY_CANDIDATES


def test_an_unsupported_source_type_is_refused_before_the_source_is_read() -> None:
    with pytest.raises(ImportExtractionError) as refusal:
        WhatsAppImportExtractor().extract(
            "vcard",
            content="ignored",
            path=None,
            self_addresses=set(),
        )

    assert refusal.value.code == "invalid_source_type"


def test_a_request_naming_more_than_one_input_is_refused_exactly_as_before() -> None:
    with pytest.raises(ImportExtractionError) as refusal:
        _extract(content="ignored", path="/nonexistent/chat.txt")

    assert refusal.value.code == "invalid_source"


requires_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")


def _served_from(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, bool]]:
    """Record, per pass, whether it was served from a path and whether from a byte snapshot."""
    served: list[tuple[bool, bool]] = []
    real = whatsapp_module.open_source_stream

    @contextmanager
    def _recording(**kwargs: Any) -> Iterator[Iterator[str]]:
        served.append((kwargs["path"] is not None, kwargs["content_bytes"] is not None))
        with real(**kwargs) as lines:
            yield lines

    monkeypatch.setattr(whatsapp_module, "open_source_stream", _recording)
    return served


def _fifo_writing(tmp_path: Path, name: str, raw: bytes) -> str:
    """Create a named pipe and start the one writer that will ever fill it."""
    fifo = tmp_path / name
    os.mkfifo(fifo)

    def write_once() -> None:
        # A reader that stops early — the byte budget refusing an oversized source — closes the
        # pipe under this writer, which is an ordinary end for it rather than a test failure.
        with suppress(BrokenPipeError), open(fifo, "wb") as handle:
            handle.write(raw)

    threading.Thread(target=write_once, daemon=True).start()
    return str(fifo)


def _extract_without_blocking(**kwargs: Any) -> Any:
    """Extract on a worker thread so a source read twice fails the test instead of hanging it.

    Opening a drained named pipe blocks until another writer arrives, and there is never another
    writer here. A regression therefore does not raise — it stops — so the timeout is the
    assertion and re-reading the pipe cannot wedge the suite.
    """
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(_extract(**kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread below
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=30)
    if worker.is_alive():
        pytest.fail("extraction blocked: the source was opened a second time")
    if isinstance(outcome[0], BaseException):
        raise outcome[0]
    return outcome[0]


@requires_fifo
def test_a_path_that_can_only_be_read_once_is_still_imported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named pipe is a supported path, and two passes over one would read it away.

    `MeteredSourceFile` documents a FIFO, a process substitution, and `/dev/stdin` as legitimate
    sources for a reader that only moves forward, and the whole-file read this replaced consumed
    one exactly once. Opening it again does not re-read it: it blocks for a writer that will not
    come, or sees end of file and looks like a source that changed. So it is snapshotted before
    the first pass, and both passes are served from that snapshot rather than from the path.
    """
    source = _fifo_writing(tmp_path, "chat.fifo", b"31/01/2024, 09:00 - Ada Lovelace: hello\n")
    served = _served_from(monkeypatch)

    extracted = _extract_without_blocking(path=source)

    assert [candidate["type"] for candidate in extracted.candidates] == ["person", "interaction"]
    assert served == [(False, True), (False, True)]


@requires_fifo
def test_a_one_shot_path_is_still_refused_when_it_is_over_the_byte_ceiling(tmp_path: Path) -> None:
    """Snapshotting must not buy back what the byte budget refuses.

    The snapshot is the same bounded read the whole-file implementation performed for every path,
    so an oversized one-shot source is refused for its size exactly as it was before.
    """
    source = _fifo_writing(tmp_path, "big.fifo", b"31/01/2024, 09:00 - Ada Lovelace: hello\n" * 64)

    with pytest.raises(ImportExtractionError) as refusal:
        _extract_without_blocking(path=source, max_source_bytes=16)

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_an_ordinary_file_is_not_snapshotted_and_is_read_from_the_path_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-shot fallback is the exception, not the rule: a regular file keeps the bound.

    Snapshotting holds the whole source, which is what this milestone exists to stop doing, so it
    must apply only where reading twice is impossible.
    """
    source = _write(tmp_path, "chat.txt", b"31/01/2024, 09:00 - Ada Lovelace: hello\n")
    served = _served_from(monkeypatch)

    _extract(path=source)

    assert served == [(True, False), (True, False)]


def test_a_missing_path_still_raises_the_error_it_always_raised(tmp_path: Path) -> None:
    """Deciding whether a path can be re-read must not change what an unreadable one reports."""
    with pytest.raises(OSError):
        _extract(path=str(tmp_path / "absent.txt"))
