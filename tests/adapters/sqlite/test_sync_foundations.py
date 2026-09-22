"""Migration, installation identity, and persisted HLC tests."""

from __future__ import annotations

from pathlib import Path

from people_context.adapters.sqlite import SqliteHybridLogicalClock, open_db


def test_fresh_database_creates_sync_schema_and_one_stable_device(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    conn = open_db(db_path)
    try:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        first = conn.execute("SELECT * FROM devices WHERE retired_at IS NULL").fetchall()
        assert {"devices", "changelog", "sync_conflicts"} <= tables
        assert "sync_peer_cursors" not in tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert len(first) == 1
        assert len(first[0]["id"]) == 26
        assert first[0]["display_name"]
    finally:
        conn.close()

    reopened = open_db(db_path)
    try:
        second = reopened.execute("SELECT id FROM devices WHERE retired_at IS NULL").fetchall()
        assert [row["id"] for row in second] == [first[0]["id"]]
    finally:
        reopened.close()


def test_hlc_orders_same_millisecond_and_survives_rollback_clock_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "hlc.db"
    conn = open_db(db_path)
    clock = SqliteHybridLogicalClock(conn, lambda: 1_000)
    first = clock.tick()
    second = clock.tick()
    device_id = clock.device_id
    conn.close()

    restarted = open_db(db_path)
    rolled_back_clock = SqliteHybridLogicalClock(restarted, lambda: 900)
    third = rolled_back_clock.tick()
    restarted.close()

    assert (first.physical_ms, first.logical_counter) == (1_000, 0)
    assert (second.physical_ms, second.logical_counter) == (1_000, 1)
    assert third.comparison_key(device_id, "op-c") > second.comparison_key(device_id, "op-b")
    assert (third.physical_ms, third.logical_counter) == (1_000, 2)
