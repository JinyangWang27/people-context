"""Single-snapshot bundle reader integration tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import SqliteBundleReader, SqlitePeopleRepository, open_db
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.relationship_vocabulary import SqliteRelationshipVocabularyStore
from people_context.domain.person import Person
from people_context.domain.relationship_vocabulary import RelationshipType
from people_context.ports.changelog import ChangelogEntry

_NOW = datetime(2026, 5, 6, 7, 8, tzinfo=UTC)
_HISTORICAL_DEVICE = "01J0000000000000000000OLD1"
_UNREFERENCED_DEVICE = "01J0000000000000000000OLD2"


def _entry(op_id: str, device_id: str, physical_ms: int, logical: int) -> ChangelogEntry:
    return ChangelogEntry(
        op_id=op_id,
        device_id=device_id,
        hlc_physical_ms=physical_ms,
        hlc_logical=logical,
        transaction_id=f"tx-{op_id}",
        entity_type="person",
        entity_id="01J000000000000000000PERS1",
        op_kind="create",
        payload={"id": "01J000000000000000000PERS1"},
        actor={"source": "test"},
        inserted_at=_NOW,
    )


def _insert_device(conn: sqlite3.Connection, device_id: str, *, retired: bool) -> None:
    conn.execute(
        """INSERT INTO devices
           (id, display_name, public_key, created_at, retired_at, hlc_physical_ms, hlc_logical)
           VALUES (?, ?, NULL, ?, ?, ?, ?)""",
        (
            device_id,
            f"device {device_id[-4:]}",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00" if retired else None,
            1_000,
            1,
        ),
    )
    conn.commit()


def _local_device_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM devices WHERE retired_at IS NULL ORDER BY created_at, id").fetchone()
    return str(row["id"])


def _advance_local_hlc(conn: sqlite3.Connection, physical_ms: int, logical: int) -> None:
    conn.execute(
        "UPDATE devices SET hlc_physical_ms = ?, hlc_logical = ? WHERE retired_at IS NULL",
        (physical_ms, logical),
    )
    conn.commit()


def test_bundle_reader_returns_every_collection_from_the_active_origin_device(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")
    local = _local_device_id(conn)
    _insert_device(conn, _HISTORICAL_DEVICE, retired=True)
    SqlitePeopleRepository(conn).save_person(Person(canonical_name="Alice", created_at=_NOW, updated_at=_NOW))
    SqliteRelationshipVocabularyStore(conn).add(
        [RelationshipType(type="co_founder_of", symmetric=True, category="professional", synonyms=["cofounder"])]
    )
    log = SqliteChangelog(conn)
    log.append(_entry("op-b", local, 2_000, 0))
    log.append(_entry("op-a", _HISTORICAL_DEVICE, 1_000, 1))
    log.append(_entry("op-c", local, 2_000, 1))
    _advance_local_hlc(conn, 2_000, 5)

    bundle = SqliteBundleReader(conn).read_bundle()
    conn.close()

    assert bundle.origin_device_id == local
    assert (bundle.watermark.physical_ms, bundle.watermark.logical_counter) == (2_000, 5)
    assert [person["canonical_name"] for person in bundle.snapshot.people] == ["Alice"]
    assert [entry.op_id for entry in bundle.changelog] == ["op-a", "op-b", "op-c"]
    assert [entry.comparison_key() for entry in bundle.changelog] == sorted(
        entry.comparison_key() for entry in bundle.changelog
    )
    assert [device["id"] for device in bundle.devices] == sorted([local, _HISTORICAL_DEVICE])
    historical = next(device for device in bundle.devices if device["id"] == _HISTORICAL_DEVICE)
    assert historical["retired_at"] == "2026-02-01T00:00:00+00:00"
    assert "co_founder_of" in [row["type"] for row in bundle.relationship_types]
    assert [row["type"] for row in bundle.relationship_types] == sorted(
        row["type"] for row in bundle.relationship_types
    )
    assert {"synonym": "cofounder", "type": "co_founder_of"} in bundle.relationship_synonyms
    assert [row["synonym"] for row in bundle.relationship_synonyms] == sorted(
        row["synonym"] for row in bundle.relationship_synonyms
    )
    assert all(
        isinstance(row["symmetric"], bool) and isinstance(row["canonical"], bool)
        for row in bundle.relationship_types
    )


def test_bundle_reader_omits_devices_no_changelog_entry_references(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")
    local = _local_device_id(conn)
    _insert_device(conn, _UNREFERENCED_DEVICE, retired=True)

    bundle = SqliteBundleReader(conn).read_bundle()
    conn.close()

    assert [device["id"] for device in bundle.devices] == [local]


def test_bundle_reader_reads_every_collection_from_one_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer committing mid-read must not appear in collections read afterwards."""
    db_path = tmp_path / "people.db"
    conn = open_db(db_path)
    local = _local_device_id(conn)
    SqliteChangelog(conn).append(_entry("op-original", local, 1_000, 0))

    concurrent = open_db(db_path)
    original_read_export = SqliteExportReader.read_export

    def _read_export_then_commit_elsewhere(self: SqliteExportReader) -> object:
        snapshot = original_read_export(self)
        SqlitePeopleRepository(concurrent).save_person(Person(canonical_name="Late", created_at=_NOW, updated_at=_NOW))
        SqliteChangelog(concurrent).append(_entry("op-late", local, 9_000, 0))
        return snapshot

    monkeypatch.setattr(SqliteExportReader, "read_export", _read_export_then_commit_elsewhere)

    bundle = SqliteBundleReader(conn).read_bundle()
    conn.close()

    assert [entry.op_id for entry in bundle.changelog] == ["op-original"]
    assert [person["canonical_name"] for person in bundle.snapshot.people] == []
    assert concurrent.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 2
    concurrent.close()
