"""Finite budgets for what an import extractor reads and produces.

The extractors read a whole local file and accumulate every candidate they find. That stays
correct for the released MCP and onboarding surfaces, whose callers already chose the file, so
every budget here takes ``None`` to mean "unbounded, exactly as before". A newer process
boundary — the M16 ``pctx import`` group — passes real budgets instead, because a mistyped or
hostile path must never become an unbounded read, and a source inside the byte ceiling can
still expand into far more candidates than the caller is willing to stage.

The byte budget is enforced by *reading* rather than by trusting ``Path.stat()``: a size an
extractor is told is not a size it has to consume, and a special file can understate its own.
Nothing here retains more than the budget, and a refusal names only the limit — never a byte
of the rejected source.

Byte budgets bound what a parser *reads*; they say nothing about what it *retains*. An
extractor that decodes its whole source and turns it into one list of records before the
first candidate exists is bounded only by a constant multiple of the read budget, which is a
much weaker promise than a staging ceiling implies. `open_source_stream` and
`ParserWorkBudget` are the two halves of the M20 answer: the reader hands an extractor its
source one line at a time under exactly the decoding rules `read_source_text` applies, and
the budget bounds the parsed records the extractor may hold live while it does.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from people_context.adapters.importers.errors import ImportExtractionError

#: Stable failure code for every source rejected by a read budget.
SOURCE_TOO_LARGE = "source_too_large"

#: Stable failure code for a source that expands past the caller's candidate ceiling.
TOO_MANY_CANDIDATES = "too_many_candidates"

#: Stable failure code for a source that is not text in the encoding its format declares.
UNDECODABLE_SOURCE = "undecodable_source"

#: Stable failure code for a parser asked to hold more live records than the caller allows.
PARSER_WORK_EXHAUSTED = "parser_work_exhausted"


def read_source_bytes(path: str, *, max_bytes: int | None) -> bytes:
    """Return the file's bytes, refusing a source larger than ``max_bytes``."""
    with Path(path).open("rb") as handle:
        if max_bytes is None:
            return handle.read()
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise source_too_large(max_bytes)
    return raw


def decode_source_bytes(raw: bytes, *, encoding: str) -> str:
    """Decode one in-memory source snapshot exactly as a path read would decode it.

    Decoding goes through a text wrapper with the default universal-newline handling, so a
    source decodes to exactly what ``Path.read_text(encoding=...)`` returns and no extractor
    sees a different string because its caller handed it bytes rather than a path.
    """
    with io.TextIOWrapper(io.BytesIO(raw), encoding=encoding, newline=None) as stream:
        try:
            return stream.read()
        except UnicodeDecodeError as exc:
            raise undecodable_source(encoding) from exc


def undecodable_source(encoding: str) -> ImportExtractionError:
    """Return the refusal for a source that is not text in the encoding its format declares.

    A file in another encoding is a source this importer cannot read, not a crash. The refusal
    names the encoding it expected and nothing about the bytes it got: the offending byte, its
    offset, and the partial line around it are all still content from an untrusted source, and
    reading incrementally must not widen what a diagnostic may carry.
    """
    return ImportExtractionError(UNDECODABLE_SOURCE, f"source is not valid {encoding} text")


def read_source_text(path: str, *, encoding: str, max_bytes: int | None) -> str:
    """Return the file's decoded text under the same read budget as `read_source_bytes`."""
    return decode_source_bytes(read_source_bytes(path, max_bytes=max_bytes), encoding=encoding)


def resolve_source_text(
    *,
    content: str | None,
    content_bytes: bytes | None,
    path: str | None,
    encoding: str,
    max_bytes: int | None,
    source_label: str,
    strip_content_bom: bool = False,
) -> str:
    """Return one source's text from exactly one of the three accepted inputs.

    ``content_bytes`` decodes through the same helper a path read uses, which is what lets a
    caller hash a snapshot and still be certain the candidates came out of those exact bytes.

    ``strip_content_bom`` covers the formats whose path encoding is ``utf-8-sig``: a decoded byte
    snapshot has already lost its byte-order mark, so only an in-memory string handed straight to
    the extractor still needs one removed.
    """
    require_one_source_input(
        content=content,
        content_bytes=content_bytes,
        path=path,
        source_label=source_label,
    )
    if content is not None:
        return content.lstrip("\ufeff") if strip_content_bom else content
    if content_bytes is not None:
        return decode_source_bytes(content_bytes, encoding=encoding)
    return read_source_text(path or "", encoding=encoding, max_bytes=max_bytes)


def require_one_source_input(
    *,
    content: str | None,
    content_bytes: bytes | None,
    path: str | None,
    source_label: str,
) -> None:
    """Refuse a request that does not name exactly one of the three accepted source inputs."""
    supplied = [value is not None for value in (content, content_bytes, path)]
    if sum(supplied) != 1:
        raise ImportExtractionError(
            "invalid_source",
            f"{source_label} import requires exactly one of content, content_bytes, or path",
        )


@contextmanager
def open_source_stream(
    *,
    content: str | None,
    content_bytes: bytes | None,
    path: str | None,
    encoding: str,
    max_bytes: int | None,
    source_label: str,
    strip_content_bom: bool = False,
    universal_newlines: bool,
) -> Iterator[Iterator[str]]:
    """Yield one source's lines without ever holding the whole decoded source.

    This is the streaming counterpart to `resolve_source_text`, and it is deliberately defined
    in terms of it: the lines it yields are exactly the lines that iterating the string that
    function returns would yield, for every one of the three accepted inputs. That equality is
    the milestone's correctness obligation, so the two knobs where the released behaviour is
    not uniform are explicit rather than inferred.

    ``universal_newlines`` is the first. A path or byte snapshot is decoded through a text
    wrapper with universal newlines, exactly as `read_source_text` decodes it, so both settings
    agree there. An in-memory ``content`` string is handed over verbatim by `resolve_source_text`
    and is then split by whichever rule its extractor applies: the line-oriented formats
    translate ``\\r\\n`` and ``\\r`` themselves before splitting, while the CSV formats feed the
    string to `csv` through a plain `io.StringIO` that splits on ``\\n`` alone. Passing the
    caller's own rule here keeps a ``\\r`` inside a quoted CSV field meaning what it means today.

    ``strip_content_bom`` is the second, and matches `resolve_source_text` exactly: a decoded
    byte snapshot has already lost its byte-order mark, so only an in-memory string still needs
    one removed.

    A decoding failure surfaces as `undecodable_source` at the line that could not be decoded,
    which is where the incremental decoder reaches the offending bytes regardless of how the
    reader chunked them.
    """
    require_one_source_input(
        content=content,
        content_bytes=content_bytes,
        path=path,
        source_label=source_label,
    )
    if content is not None:
        text = content.lstrip("\ufeff") if strip_content_bom else content
        with io.StringIO(text, newline=None if universal_newlines else "\n") as stream:
            yield iter(stream)
        return
    if content_bytes is not None:
        with io.TextIOWrapper(io.BytesIO(content_bytes), encoding=encoding, newline=None) as stream:
            yield _decoded_lines(stream, encoding)
        return
    source_path = path or ""
    # The cheap first answer, so a source already past the byte ceiling is refused for the
    # reason it is oversized rather than for whatever a partial parse of it hits first.
    refuse_oversized_file(source_path, max_bytes=max_bytes)
    with Path(source_path).open("rb") as handle:
        metered = MeteredSourceFile(handle, SourceReadBudget(max_bytes))
        with io.TextIOWrapper(metered, encoding=encoding, newline=None) as stream:
            yield _decoded_lines(stream, encoding)


def _decoded_lines(stream: Iterable[str], encoding: str) -> Iterator[str]:
    """Yield a stream's lines, turning an incremental decode failure into a stable refusal."""
    iterator = iter(stream)
    while True:
        try:
            line = next(iterator)
        except StopIteration:
            return
        except UnicodeDecodeError as exc:
            raise undecodable_source(encoding) from exc
        yield line


def drain_source(lines: Iterable[str]) -> None:
    """Consume what is left of a source so a read or decode refusal still outranks a parse one.

    A whole-file read decoded the entire source *before* any parser looked at it, so a source
    that was both unparseable and undecodable was always refused as undecodable, and one past
    the byte ceiling was always refused as oversized. Streaming reverses that by construction:
    the parser reaches its own objection first and would report it instead.

    Draining restores the released precedence without restoring the whole-file read. It runs
    only on the refusal path, where the old implementation had already paid for the entire
    source anyway, and it retains nothing: if the rest of the source cannot be read or decoded,
    that refusal is raised from here and replaces the parser's.
    """
    for _ in lines:
        pass


def iter_split_lines(lines: Iterable[str]) -> Iterator[str]:
    """Yield exactly what ``text.split("\\n")`` yields, one line at a time.

    The line-oriented extractors index and skip by position in that split, so streaming has to
    reproduce it element for element — including the trailing empty string a newline-terminated
    source produces, and the single empty string an empty source produces.
    """
    terminated = True
    for line in lines:
        terminated = line.endswith("\n")
        yield line[:-1] if terminated else line
    if terminated:
        yield ""


def refuse_oversized_file(path: str, *, max_bytes: int | None) -> None:
    """Refuse a source whose reported size is already past the budget, before opening it.

    This is only the cheap first answer, and it is deliberately not the guarantee: a file can
    grow, and a special file can report nothing. `MeteredSourceFile` is what actually bounds
    the read; this just avoids paying for any of that machinery on an obviously oversized file.
    """
    if max_bytes is None:
        return
    if os.stat(path).st_size > max_bytes:
        raise source_too_large(max_bytes)


class SourceReadBudget:
    """Refuses once a reader has consumed past ``max_bytes`` of one source."""

    def __init__(self, max_bytes: int | None) -> None:
        self._max_bytes = max_bytes

    def cap(self, position: int, size: int) -> int:
        """Return a read size that cannot take a reader more than one byte past the budget.

        Metering a read after it returns is too late to bound the allocation it made: an
        unterminated line is one `readline(-1)` that materializes the rest of the file before
        anything can refuse it. Capping at the remaining allowance plus one byte keeps that
        allocation finite while still overshooting the ceiling, so `observe` still refuses.
        Truncating the returned bytes is harmless precisely because a read that hits the cap
        is a read whose caller is about to be refused.
        """
        if self._max_bytes is None:
            return size
        allowed = self._max_bytes - position + 1
        return allowed if size < 0 else min(size, allowed)

    def observe(self, position: int) -> None:
        """Record how far into the source a reader has now read, refusing past the budget."""
        if self._max_bytes is not None and position > self._max_bytes:
            raise source_too_large(self._max_bytes)


class MeteredSourceFile:
    """A read-through view of a source file that refuses past the caller's budget.

    Some readers own the file rather than being handed its bytes: `mailbox.mbox` opens the
    path itself and scans the whole thing to build its table of contents before it yields a
    single message. A budget applied at one call site would meter none of that, so it is
    applied to the file object the reader actually reads through.

    The measure is the furthest offset reached, not a running total of bytes returned, because
    a reader that seeks back over bytes it already read — as `mailbox` does when it re-reads
    each message after the scan — has not read any more of the file.

    Every read is also capped before it runs, so the budget bounds the allocation and not just
    the verdict: a source that grew into one enormous unterminated line cannot be pulled into
    memory in a single `readline` and refused afterwards.
    """

    def __init__(self, inner: IO[bytes], budget: SourceReadBudget) -> None:
        self._inner = inner
        self._budget = budget

    def read(self, size: int = -1) -> bytes:
        data = self._inner.read(self._budget.cap(self._inner.tell(), size))
        self._budget.observe(self._inner.tell())
        return data

    def readline(self, size: int = -1) -> bytes:
        data = self._inner.readline(self._budget.cap(self._inner.tell(), size))
        self._budget.observe(self._inner.tell())
        return data

    def read1(self, size: int = -1) -> bytes:
        """Meter the one read a `TextIOWrapper` prefers, rather than delegating past the budget.

        A text wrapper asks its buffer for `read1` whenever the buffer has one, and everything
        this class does not name explicitly is delegated to the file underneath. Leaving this
        one implicit would hand a decoding reader an unmetered path through the budget — the
        exact hole the class exists to close. Declaring it here makes it always present, so a
        source object without one falls back to the ordinary read rather than failing.
        """
        read1 = getattr(self._inner, "read1", self._inner.read)
        data = read1(self._budget.cap(self._inner.tell(), size))
        self._budget.observe(self._inner.tell())
        return data

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else — `seek`, `tell`, `close`, `fileno`, `name` — unchanged."""
        return getattr(self._inner, name)


class CandidateBudget:
    """Refuses once extraction has produced more candidates than the caller will stage.

    The byte ceiling on a source does not imply a ceiling on what it expands into: a dense
    contacts export packs a candidate into a few dozen bytes, so a file well inside the read
    budget can still yield millions of them. Extractors account as they accumulate, which is
    what turns the staging ceiling into a bound on memory rather than a check applied to a
    list that has already been built.
    """

    def __init__(self, max_candidates: int | None) -> None:
        self._max_candidates = max_candidates

    def account(self, count: int) -> None:
        """Record the candidates accumulated so far, refusing as soon as they pass the limit."""
        if self._max_candidates is not None and count > self._max_candidates:
            raise ImportExtractionError(
                TOO_MANY_CANDIDATES,
                f"source produces more than the {self._max_candidates} candidates this command stages",
            )


class ParserWorkBudget:
    """Refuses once a parser holds more live parsed records than the caller allows.

    This is the third and narrowest of the budgets, and the only one that is not an input
    limit. The other two bound what a source may *be*: how many bytes an extractor reads and
    how many candidates it may expand into. Neither says anything about the interval between
    them, where a parser turns the whole source into intermediate records before there is a
    single candidate to count — and a skipped record produces no candidate at all, so a source
    that stages nothing could still retain one object per line of input.

    What is metered is therefore how many parsed records are held *live at once*, not how many
    were seen: a streamed-and-discarded record costs nothing, so a million skips stay free
    while one pathological record that keeps growing is refused. Extractors account as they
    accumulate, which is what makes the ceiling a bound on retention rather than a check
    applied to a structure that has already been built.

    ``None`` is unbounded and is the default everywhere, exactly as the byte and candidate
    budgets were introduced: only a boundary that chose a ceiling is bounded by one.
    """

    def __init__(self, max_records: int | None) -> None:
        self._max_records = max_records

    def account(self, retained: int) -> None:
        """Record how many parsed records are live now, refusing as soon as they pass the limit."""
        if self._max_records is not None and retained > self._max_records:
            raise ImportExtractionError(
                PARSER_WORK_EXHAUSTED,
                f"source needs more than the {self._max_records} parsed records this command holds at once",
            )


def source_too_large(max_bytes: int) -> ImportExtractionError:
    """Return the refusal for an over-budget source, naming the limit and nothing else."""
    return ImportExtractionError(
        SOURCE_TOO_LARGE,
        f"source exceeds the {max_bytes} byte import limit for this command",
    )
