"""`pctx sync push` CLI tests."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app.exports import SYNC_BUNDLE_FILENAME
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.cli import main
from people_context.domain.sync_bundle import SYNC_BUNDLE_FORMAT, SYNC_BUNDLE_VERSION, SyncBundleDocument

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _seed(db_path: Path) -> None:
    conn = open_db(db_path)
    repository = SqlitePeopleRepository(conn)
    RememberPerson(repository, repository, SqliteAuditLog(conn), _Clock()).execute(RememberPersonInput(name="Alice"))
    conn.close()


def test_sync_push_writes_an_owner_only_bundle_and_reports_counts(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "outbox"
    _seed(db_path)

    assert main(["--db", str(db_path), "sync", "push", "--output", str(output)]) == 0

    bundle = output / SYNC_BUNDLE_FILENAME
    assert stat.S_IMODE(os.stat(bundle).st_mode) == 0o600
    document = SyncBundleDocument.model_validate(json.loads(bundle.read_text(encoding="utf-8")))
    assert document.format == SYNC_BUNDLE_FORMAT
    assert document.version == SYNC_BUNDLE_VERSION
    assert [person.canonical_name for person in document.snapshot.people] == ["Alice"]
    assert len(document.changelog) == 1
    assert document.devices[0].id == document.origin_device_id

    out = capsys.readouterr().out
    assert str(bundle) in out
    assert "People 1," in out
    assert "changelog entries 1." in out
    assert f"watermark {document.watermark.hlc_physical_ms}/{document.watermark.hlc_logical}." in out
    assert "encrypted" in out


def test_sync_push_creates_a_missing_output_directory(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    output = tmp_path / "nested" / "outbox"

    assert main(["--db", str(db_path), "sync", "push", "--output", str(output)]) == 0

    capsys.readouterr()
    assert (output / SYNC_BUNDLE_FILENAME).is_file()


def test_sync_push_replaces_a_previous_bundle_without_retaining_its_mode(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "outbox"
    output.mkdir()
    stale = output / SYNC_BUNDLE_FILENAME
    stale.write_text("stale\n", encoding="utf-8")
    # Differs from 0o600 so a mode-retaining write is caught, while granting nothing to group or
    # other: CodeQL `security-extended` reports any non-owner bit, even in a test fixture.
    os.chmod(stale, 0o700)
    _seed(db_path)

    assert main(["--db", str(db_path), "sync", "push", "--output", str(output)]) == 0

    capsys.readouterr()
    assert stat.S_IMODE(os.stat(stale).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(stale).st_mode) & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert json.loads(stale.read_text(encoding="utf-8"))["format"] == SYNC_BUNDLE_FORMAT


def test_sync_push_reports_an_unusable_output_location_without_a_traceback(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    blocked = tmp_path / "outbox"
    blocked.write_text("not a directory\n", encoding="utf-8")

    assert main(["--db", str(db_path), "sync", "push", "--output", str(blocked)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Cannot write the sync bundle" in captured.err
    assert blocked.read_text(encoding="utf-8") == "not a directory\n"


def test_sync_requires_a_subcommand_and_an_output_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)

    with pytest.raises(SystemExit):
        main(["--db", str(db_path), "sync"])
    with pytest.raises(SystemExit):
        main(["--db", str(db_path), "sync", "push"])
