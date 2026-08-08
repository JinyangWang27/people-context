"""Deterministic local tail of the replayable changelog.

This is a read-only projection over rows the store already holds: it records nothing,
mints no audit or changelog rows, and keeps no cursor across invocations. Every poll is
bounded, and the cursor only ever advances to an entry the caller has actually received.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

from pydantic import BaseModel, Field

from people_context.ports.changelog import Changelog, ChangelogCursor, ChangelogEntry
from people_context.ports.sleep import Sleeper

MIN_INTERVAL_SECONDS = 0.1
MAX_INTERVAL_SECONDS = 3600.0
DEFAULT_INTERVAL_SECONDS = 2.0

# One poll reads at most this many entries, so a tail over a long history stays bounded in
# memory and emits its first batch promptly. A full batch means more may already be waiting,
# so the stream polls again immediately instead of sleeping.
WATCH_BATCH_SIZE = 200


class WatchChangelogError(ValueError):
    """Raised when a watch parameter falls outside its documented range."""


class WatchPoll(BaseModel):
    """The entries one bounded poll returned and the cursor to resume from."""

    entries: list[ChangelogEntry] = Field(default_factory=list)
    cursor: ChangelogCursor | None = None

    @property
    def saturated(self) -> bool:
        """Return whether the poll filled its batch, meaning more entries may be waiting."""
        return len(self.entries) >= WATCH_BATCH_SIZE


class WatchChangelog:
    """Follow the local changelog by repeatedly polling for entries after a cursor.

    Polling mechanics are split so they can be tested without a long-running process:
    `start_cursor` and `poll` are single deterministic steps that never sleep, and only
    `stream` composes them into a loop around the injected sleeper.
    """

    def __init__(self, changelog: Changelog, sleeper: Sleeper) -> None:
        self._changelog = changelog
        self._sleeper = sleeper

    def start_cursor(self, *, from_start: bool = False) -> ChangelogCursor | None:
        """Return the cursor a tail begins at, reading the current latest entry once.

        By default the tail reports only what happens from now on, so it starts at the
        newest existing entry and emits no history. `from_start` returns `None`, which
        sorts before the minimum key and therefore replays everything. An empty changelog
        yields `None` either way, because there is no history to withhold.
        """
        if from_start:
            return None
        latest = self._changelog.list_entries(limit=1)
        return latest[0].comparison_key() if latest else None

    def poll(self, cursor: ChangelogCursor | None) -> WatchPoll:
        """Read one bounded batch after `cursor` and report where to resume.

        The cursor advances only to the last entry actually returned, so an empty poll
        leaves it untouched and no entry can be skipped by a poll that returned nothing.
        """
        entries = self._changelog.list_entries_after(cursor, limit=WATCH_BATCH_SIZE)
        resume = entries[-1].comparison_key() if entries else cursor
        return WatchPoll(entries=entries, cursor=resume)

    def stream(
        self,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        from_start: bool = False,
    ) -> Iterator[ChangelogEntry]:
        """Yield changelog entries in replication order as they appear, forever.

        The interval is validated before any read, so a rejected interval raises here
        rather than on the first entry the caller pulls. The returned iterator is lazy
        and unbounded: the caller decides when to stop consuming it.
        """
        _validate_interval(interval_seconds)
        return self._iterate(interval_seconds=interval_seconds, from_start=from_start)

    def _iterate(self, *, interval_seconds: float, from_start: bool) -> Iterator[ChangelogEntry]:
        cursor = self.start_cursor(from_start=from_start)
        while True:
            batch = self.poll(cursor)
            cursor = batch.cursor
            yield from batch.entries
            if not batch.saturated:
                # A partial batch means the tail has caught up; a full one may have more
                # waiting, and sleeping then would stall a replay one batch at a time.
                self._sleeper.sleep(interval_seconds)


def _validate_interval(interval_seconds: float) -> None:
    if not math.isfinite(interval_seconds):
        raise WatchChangelogError("interval must be a finite number of seconds")
    if interval_seconds < MIN_INTERVAL_SECONDS or interval_seconds > MAX_INTERVAL_SECONDS:
        raise WatchChangelogError(f"interval must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS} seconds")
