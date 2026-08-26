"""A finite read budget for the local files import extractors parse.

The extractors read a whole local file. That stays correct for the released MCP and
onboarding surfaces, whose callers already chose the file, so every helper here takes
``max_bytes=None`` to mean "unbounded, exactly as before". A newer process boundary — the
M16 ``pctx import`` group — passes a real budget instead, because a mistyped or hostile
path must never become an unbounded read.

The budget is enforced by *reading* at most one byte past the limit rather than by trusting
``Path.stat()``: a size an extractor is told is not a size it has to consume, and a special
file can understate its own. Nothing here retains more than the budget, and a refusal names
only the limit — never a byte of the rejected source.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from people_context.adapters.importers.errors import ImportExtractionError

#: Stable failure code for every source rejected by a read budget.
SOURCE_TOO_LARGE = "source_too_large"

_CHUNK_BYTES = 1 << 20


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
        return stream.read()


def verify_source_size(path: str, *, max_bytes: int | None) -> None:
    """Refuse a path-only source larger than ``max_bytes`` before anything iterates it.

    Some sources — `mbox` — are handed to a reader that owns the file itself, so the budget
    cannot be applied at the point of the read. It is applied here instead, by streaming the
    file and discarding it: the check costs a bounded read and no proportional memory, and it
    stops at the first byte past the limit.
    """
    if max_bytes is None:
        return
    remaining = max_bytes + 1
    with Path(path).open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(remaining, _CHUNK_BYTES))
            if not chunk:
                return
            remaining -= len(chunk)
    raise source_too_large(max_bytes)


def refuse_grown_source(path: str, *, max_bytes: int | None) -> None:
    """Refuse a path-only source that grew past the budget while it was being processed.

    `verify_source_size` describes the file at one instant. A file that is still being
    written can pass that check and then hand the reader far more, so the same budget is
    re-asserted once processing finishes and before any candidate is staged.
    """
    if max_bytes is None:
        return
    if os.stat(path).st_size > max_bytes:
        raise source_too_large(max_bytes)


class SourceByteBudget:
    """Cumulative counter that refuses once a streamed source passes its budget."""

    def __init__(self, max_bytes: int | None) -> None:
        self._max_bytes = max_bytes
        self._consumed = 0

    def consume(self, count: int) -> None:
        """Record ``count`` consumed bytes, refusing as soon as the budget is exceeded."""
        if self._max_bytes is None:
            return
        self._consumed += count
        if self._consumed > self._max_bytes:
            raise source_too_large(self._max_bytes)


def source_too_large(max_bytes: int) -> ImportExtractionError:
    """Return the refusal for an over-budget source, naming the limit and nothing else."""
    return ImportExtractionError(
        SOURCE_TOO_LARGE,
        f"source exceeds the {max_bytes} byte import limit for this command",
    )
