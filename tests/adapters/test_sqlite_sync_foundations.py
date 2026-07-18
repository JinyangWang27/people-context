"""Migration, installation identity, and persisted HLC tests."""

from __future__ import annotations

import sqlite3
from importlib import resources
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
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


def test_m4_database_upgrades_without_inventing_changelog_history(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.db"
    conn = sqlite3.connect(db_path)
    migration = resources.files("people_context.adapters.sqlite.migrations").joinpath("001_initial.sql")
    conn.executescript(migration.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        """INSERT INTO persons
           (id, canonical_name, canonical_name_normalized, is_self, summary, created_at, updated_at, deleted_at)
           VALUES ('01J00000000000000000000000', 'Historical Alice', 'historical alice', 0, NULL,
                   '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', NULL)"""
    )
    conn.commit()
    conn.close()

    upgraded = open_db(db_path)
    try:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 2
        assert upgraded.execute("SELECT canonical_name FROM persons").fetchone()[0] == "Historical Alice"
        assert upgraded.execute("SELECT COUNT(*) FROM devices WHERE retired_at IS NULL").fetchone()[0] == 1
        assert upgraded.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
    finally:
        upgraded.close()


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
