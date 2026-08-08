"""CLI behaviour for the `pctx watch` changelog tail (M13.4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import SqliteChangelog, open_db
from people_context.app.sync import WATCH_BATCH_SIZE
from people_context.cli import main
from people_context.ports.changelog import ChangelogEntry

_INSERTED_AT = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


class _StopSleeper:
    """A sleeper that ends the tail the way an operator does, at the first idle pause."""

    def __init__(self) -> None:
        self.pauses: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        raise KeyboardInterrupt


class _WritingSleeper(_StopSleeper):
    """A sleeper that appends one entry during its first pause, then stops at the next."""

    def __init__(self, db_path: Path, op_id: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._op_id = op_id

    def sleep(self, seconds: float) -> None:
        if not self.pauses:
            self.pauses.append(seconds)
            _append(self._db_path, self._op_id, index=99)
            return
        super().sleep(seconds)


def _entry(op_id: str, device_id: str, index: int) -> ChangelogEntry:
    return ChangelogEntry(
        op_id=op_id,
        device_id=device_id,
        hlc_physical_ms=1_700_000_000_000 + index,
        hlc_logical=0,
        transaction_id=f"txn-{op_id}",
        entity_type="person",
        entity_id="person-1",
        op_kind="update",
        payload={"summary": "written by the tail test"},
        changed_fields=["summary"],
        inserted_at=_INSERTED_AT,
    )


def _active_device(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT id FROM devices WHERE retired_at IS NULL").fetchone()["id"])


def _append(db_path: Path, op_id: str, *, index: int) -> None:
    """Append one entry through an independent connection, as another process would."""
    conn = open_db(db_path)
    try:
        SqliteChangelog(conn).append(_entry(op_id, _active_device(conn), index))
    finally:
        conn.close()


def _seed(db_path: Path, count: int) -> list[str]:
    conn = open_db(db_path)
    try:
        changelog = SqliteChangelog(conn)
        device_id = _active_device(conn)
        op_ids = [f"op-{index:04d}" for index in range(count)]
        for index, op_id in enumerate(op_ids):
            changelog.append(_entry(op_id, device_id, index))
        return op_ids
    finally:
        conn.close()


def _install(monkeypatch: pytest.MonkeyPatch, sleeper: _StopSleeper) -> None:
    """Replace the runtime's real sleeper so the tail never waits on wall-clock time."""
    monkeypatch.setattr("people_context.adapters.runtime.SystemSleeper", lambda: sleeper)


def _emitted(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines() if line]


def test_watch_refuses_an_interval_below_the_documented_minimum(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, 2)
    sleeper = _StopSleeper()
    _install(monkeypatch, sleeper)

    code = main(["--db", str(db_path), "watch", "--interval", "0.05"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "interval must be between" in captured.err
    # Nothing was polled and nothing was waited on.
    assert sleeper.pauses == []


def test_watch_refuses_an_interval_above_the_documented_maximum(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, 1)
    _install(monkeypatch, _StopSleeper())

    code = main(["--db", str(db_path), "watch", "--interval", "3600.5"])

    assert code == 2
    assert "interval must be between" in capsys.readouterr().err


def test_watch_emits_no_history_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, 5)
    sleeper = _StopSleeper()
    _install(monkeypatch, sleeper)

    code = main(["--db", str(db_path), "watch", "--interval", "0.25"])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert sleeper.pauses == [0.25]
    assert "may contain sensitive personal data" in captured.err
    assert "Stopped." in captured.err


def test_watch_emits_an_entry_written_after_the_tail_started(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, 3)
    _install(monkeypatch, _WritingSleeper(db_path, "op-live"))

    code = main(["--db", str(db_path), "watch"])

    captured = capsys.readouterr()
    assert code == 0
    assert [entry["op_id"] for entry in _emitted(captured.out)] == ["op-live"]


def test_watch_from_start_replays_history_in_replication_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    op_ids = _seed(db_path, 4)
    _install(monkeypatch, _StopSleeper())

    code = main(["--db", str(db_path), "watch", "--from-start"])

    captured = capsys.readouterr()
    emitted = _emitted(captured.out)
    assert code == 0
    assert [entry["op_id"] for entry in emitted] == op_ids
    assert captured.out.count("\n") == len(op_ids)


def test_watch_from_start_drains_more_than_one_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    op_ids = _seed(db_path, WATCH_BATCH_SIZE + 6)
    _install(monkeypatch, _StopSleeper())

    code = main(["--db", str(db_path), "watch", "--from-start"])

    assert code == 0
    assert [entry["op_id"] for entry in _emitted(capsys.readouterr().out)] == op_ids


def test_each_emitted_line_is_one_canonical_json_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path, 1)
    _install(monkeypatch, _StopSleeper())

    main(["--db", str(db_path), "watch", "--from-start"])

    line = capsys.readouterr().out.splitlines()[0]
    entry = json.loads(line)
    assert set(entry) == {
        "op_id",
        "device_id",
        "hlc_physical_ms",
        "hlc_logical",
        "transaction_id",
        "entity_type",
        "entity_id",
        "op_kind",
        "payload",
        "changed_fields",
        "actor",
        "schema_version",
        "inserted_at",
    }
    assert entry["payload"] == {"summary": "written by the tail test"}
    # Canonical: sorted keys, no interior padding, and one object per physical line.
    assert line == json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_watch_on_an_empty_changelog_emits_nothing_and_exits_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "people.db"
    open_db(db_path).close()
    _install(monkeypatch, _StopSleeper())

    code = main(["--db", str(db_path), "watch", "--from-start"])

    assert code == 0
    assert capsys.readouterr().out == ""
