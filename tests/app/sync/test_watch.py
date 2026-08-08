"""Polling policy for the local changelog tail (M13.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import islice

import pytest

from people_context.app.sync import (
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    WATCH_BATCH_SIZE,
    WatchChangelog,
    WatchChangelogError,
)
from people_context.ports.changelog import ChangelogEntry
from tests.app.fakes import FakeChangelog, FakeSleeper

_INSERTED_AT = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _entry(index: int, *, device_id: str = "device-a", logical: int = 0) -> ChangelogEntry:
    """Build one deterministic entry whose comparison key is driven by `index`."""
    return ChangelogEntry(
        op_id=f"op-{index:04d}",
        device_id=device_id,
        hlc_physical_ms=1_700_000_000_000 + index,
        hlc_logical=logical,
        transaction_id=f"txn-{index:04d}",
        entity_type="person",
        entity_id="person-1",
        op_kind="update",
        payload={"index": index},
        inserted_at=_INSERTED_AT,
    )


def _watch(changelog: FakeChangelog, sleeper: FakeSleeper | None = None) -> WatchChangelog:
    return WatchChangelog(changelog, sleeper or FakeSleeper())


@pytest.mark.parametrize("interval", [0.0, 0.09, MAX_INTERVAL_SECONDS + 0.1, -1.0, float("nan"), float("inf")])
def test_stream_rejects_an_interval_outside_the_documented_range(interval: float) -> None:
    changelog = FakeChangelog([_entry(1)])

    with pytest.raises(WatchChangelogError):
        _watch(changelog).stream(interval_seconds=interval)

    # Validation happens before the first read, so no cursor was established.
    assert changelog.after_calls == []


@pytest.mark.parametrize("interval", [MIN_INTERVAL_SECONDS, DEFAULT_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS])
def test_stream_accepts_both_interval_boundaries(interval: float) -> None:
    changelog = FakeChangelog([_entry(1)])
    sleeper = FakeSleeper(stop_after=1)

    stream = _watch(changelog, sleeper).stream(interval_seconds=interval, from_start=True)
    with pytest.raises(FakeSleeper.Stopped):
        list(stream)

    assert sleeper.pauses == [interval]


def test_default_start_cursor_is_the_latest_entry_so_history_is_not_replayed() -> None:
    entries = [_entry(index) for index in range(5)]
    changelog = FakeChangelog(entries)
    watch = _watch(changelog)

    assert watch.start_cursor() == entries[-1].comparison_key()
    assert watch.poll(watch.start_cursor()).entries == []

    changelog.append(_entry(9))
    assert [entry.op_id for entry in watch.poll(watch.start_cursor(from_start=False)).entries] == []


class _AppendingSleeper(FakeSleeper):
    """A sleeper that writes one entry during its first pause.

    The pause is the only moment a real tail is idle, so it is where a concurrent write
    belongs in a deterministic test.
    """

    def __init__(self, changelog: FakeChangelog, entry: ChangelogEntry, stop_after: int) -> None:
        super().__init__(stop_after=stop_after)
        self._changelog = changelog
        self._entry = entry

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if len(self.pauses) == 1:
            self._changelog.append(self._entry)


def test_default_start_emits_only_entries_appended_after_the_tail_began() -> None:
    changelog = FakeChangelog([_entry(index) for index in range(3)])
    sleeper = _AppendingSleeper(changelog, _entry(7), stop_after=2)
    stream = _watch(changelog, sleeper).stream(interval_seconds=0.5)

    # The first poll establishes the cursor and finds nothing; the entry written during
    # that first pause is the only one the tail should ever emit.
    emitted: list[str] = []
    with pytest.raises(FakeSleeper.Stopped):
        for entry in stream:
            emitted.append(entry.op_id)

    assert emitted == ["op-0007"]
    assert sleeper.pauses == [0.5, 0.5]


def test_from_start_replays_every_existing_entry_in_replication_order() -> None:
    entries = [_entry(index) for index in range(4)]
    # Appended newest first, so a correct implementation must sort rather than echo order.
    changelog = FakeChangelog(list(reversed(entries)))
    stream = _watch(changelog).stream(from_start=True)

    replayed = list(islice(stream, len(entries)))

    assert [entry.op_id for entry in replayed] == [entry.op_id for entry in entries]
    assert changelog.after_calls[0] == (None, WATCH_BATCH_SIZE)


def test_an_empty_changelog_starts_at_the_beginning_under_either_mode() -> None:
    watch = _watch(FakeChangelog())

    assert watch.start_cursor() is None
    assert watch.start_cursor(from_start=True) is None


def test_a_poll_is_bounded_by_the_batch_size_and_reports_saturation() -> None:
    changelog = FakeChangelog([_entry(index) for index in range(WATCH_BATCH_SIZE + 5)])

    batch = _watch(changelog).poll(None)

    assert len(batch.entries) == WATCH_BATCH_SIZE
    assert batch.saturated
    assert batch.cursor == batch.entries[-1].comparison_key()
    assert changelog.after_calls == [(None, WATCH_BATCH_SIZE)]


def test_an_empty_poll_leaves_the_cursor_untouched() -> None:
    entries = [_entry(index) for index in range(2)]
    changelog = FakeChangelog(entries)
    cursor = entries[-1].comparison_key()

    batch = _watch(changelog).poll(cursor)

    assert batch.entries == []
    assert batch.cursor == cursor
    assert not batch.saturated


def test_a_multi_batch_replay_advances_the_cursor_without_pausing_between_batches() -> None:
    total = WATCH_BATCH_SIZE * 2 + 3
    changelog = FakeChangelog([_entry(index) for index in range(total)])
    # Stopping at the very first pause proves the drain never paused between batches.
    sleeper = FakeSleeper(stop_after=0)
    stream = _watch(changelog, sleeper).stream(interval_seconds=0.25, from_start=True)

    replayed: list[str] = []
    with pytest.raises(FakeSleeper.Stopped):
        for entry in stream:
            replayed.append(entry.op_id)

    assert replayed == [f"op-{index:04d}" for index in range(total)]
    requested = [cursor for cursor, _limit in changelog.after_calls]
    assert requested[0] is None
    assert requested[1] == (1_700_000_000_000 + WATCH_BATCH_SIZE - 1, 0, "device-a", f"op-{WATCH_BATCH_SIZE - 1:04d}")
    assert requested[2] == (
        1_700_000_000_000 + WATCH_BATCH_SIZE * 2 - 1,
        0,
        "device-a",
        f"op-{WATCH_BATCH_SIZE * 2 - 1:04d}",
    )
    # Three polls drained the history back to back; the pause came only once it was empty.
    assert len(changelog.after_calls) == 3
    assert sleeper.pauses == []


def test_every_entry_is_emitted_exactly_once_across_batches() -> None:
    total = WATCH_BATCH_SIZE + 7
    changelog = FakeChangelog([_entry(index) for index in range(total)])
    stream = _watch(changelog).stream(from_start=True)

    emitted = [entry.op_id for entry in islice(stream, total)]

    assert emitted == [f"op-{index:04d}" for index in range(total)]
    assert len(set(emitted)) == total


def test_the_cursor_compares_the_full_key_so_a_device_tie_is_not_skipped() -> None:
    # Both entries share one HLC pair; only the device id separates them.
    first = _entry(1, device_id="device-a")
    second = _entry(1, device_id="device-b")
    changelog = FakeChangelog([second, first])
    watch = _watch(changelog)

    batch = watch.poll(first.comparison_key())

    assert [entry.device_id for entry in batch.entries] == ["device-b"]
    assert watch.start_cursor() == second.comparison_key()
