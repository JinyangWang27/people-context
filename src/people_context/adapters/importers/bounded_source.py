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
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import IO, Any

from people_context.adapters.importers.errors import ImportExtractionError

#: Stable failure code for every source rejected by a read budget.
SOURCE_TOO_LARGE = "source_too_large"

#: Stable failure code for a source that expands past the caller's candidate ceiling.
TOO_MANY_CANDIDATES = "too_many_candidates"

#: Stable failure code for a source that is not text in the encoding its format declares.
UNDECODABLE_SOURCE = "undecodable_source"


def read_source_bytes(path: str, *, max_bytes: int | None) -> bytes:
    """Return the file's bytes, refusing a source larger than ``max_bytes``."""
    with Path(path).open("rb") as handle:
        if max_bytes is None:
            return handle.read()
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise source_too_large(max_bytes)
    return raw


def read_source_text(path: str, *, encoding: str, max_bytes: int | None) -> str:
    """Return the file's decoded text under the same read budget as `read_source_bytes`.

    Decoding goes through a text wrapper with the default universal-newline handling, so a
    source inside the budget decodes to exactly what ``Path.read_text(encoding=...)`` returns
    and no extractor sees a different string because its caller supplied a budget.
    """
    raw = read_source_bytes(path, max_bytes=max_bytes)
    with io.TextIOWrapper(io.BytesIO(raw), encoding=encoding, newline=None) as stream:
        try:
            return stream.read()
        except UnicodeDecodeError as exc:
            # A file in another encoding is a source this importer cannot read, not a crash.
            # The refusal names the encoding it expected and nothing about the bytes it got:
            # the offending byte and its offset are still content from an untrusted source.
            raise ImportExtractionError(
                UNDECODABLE_SOURCE,
                f"source is not valid {encoding} text",
            ) from exc


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


def source_too_large(max_bytes: int) -> ImportExtractionError:
    """Return the refusal for an over-budget source, naming the limit and nothing else."""
    return ImportExtractionError(
        SOURCE_TOO_LARGE,
        f"source exceeds the {max_bytes} byte import limit for this command",
    )
