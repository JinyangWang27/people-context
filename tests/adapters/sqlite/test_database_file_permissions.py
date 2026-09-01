"""The database file itself is owner-only, like every export derived from it.

`atomic_write_private_text` already guarantees `0600` for briefs, vCards, vaults, ICS
files, and sync bundles. The store those are projected from holds strictly more, so a
world-readable database would leave the weakest link at the centre rather than the edge.

These assertions are POSIX-only. On Windows the `mode` argument to `os.open` sets the
read-only attribute rather than installing an owner-only DACL, so the file inherits its
directory's ACL and there is no `0600` to assert; that platform is documented as relying
on the profile directory's permissions and the encrypted extra instead.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from people_context.adapters.filesystem.private_file import PRIVATE_FILE_MODE
from people_context.adapters.sqlite import open_db

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file modes; Windows ACLs are not set by os.open's mode argument",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_newly_created_database_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"

    conn = open_db(db_path)
    conn.close()

    assert db_path.exists()
    assert _mode(db_path) == PRIVATE_FILE_MODE


def test_a_new_database_stays_owner_only_under_a_permissive_umask(tmp_path: Path) -> None:
    """A `0o000` umask removes nothing, so the mode has to come from the open itself."""
    db_path = tmp_path / "permissive.db"
    previous = os.umask(0o000)
    try:
        conn = open_db(db_path)
        conn.close()
    finally:
        os.umask(previous)

    assert _mode(db_path) == PRIVATE_FILE_MODE


def test_the_write_ahead_log_and_shared_memory_files_are_owner_only(tmp_path: Path) -> None:
    """WAL and SHM carry the same pages, so they must not be more readable than the store."""
    db_path = tmp_path / "wal.db"
    conn = open_db(db_path)
    try:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        conn.commit()
        sidecars = [tmp_path / "wal.db-wal", tmp_path / "wal.db-shm"]
        present = [path for path in sidecars if path.exists()]
        assert present, "WAL mode should have produced at least one sidecar file"
        for path in present:
            assert _mode(path) == PRIVATE_FILE_MODE
    finally:
        conn.close()


def test_an_existing_database_keeps_the_mode_its_operator_chose(tmp_path: Path) -> None:
    """Opening a store must not silently rewrite permissions someone set deliberately."""
    db_path = tmp_path / "existing.db"
    open_db(db_path).close()
    db_path.chmod(0o644)

    conn = open_db(db_path)
    conn.close()

    assert _mode(db_path) == 0o644


def test_opening_the_in_memory_database_creates_no_file(tmp_path: Path) -> None:
    conn = open_db(":memory:")
    conn.close()

    assert list(tmp_path.iterdir()) == []


def test_a_dangling_symlink_target_is_created_owner_only(tmp_path: Path) -> None:
    """`O_EXCL` refuses to follow a symlink, so the check has to resolve one first.

    A dangling link reports `FileExistsError` for the *link*, which would read as "a
    database is already there". SQLite then follows the link and creates the real target
    with its own `0o644`, quietly bypassing the guarantee for anyone who points `--db` at
    a symlink — a normal way to keep the store on another volume.
    """
    target = tmp_path / "elsewhere" / "target.db"
    target.parent.mkdir()
    link = tmp_path / "people.db"
    link.symlink_to(target)
    assert not target.exists()

    conn = open_db(link)
    conn.close()

    assert target.exists()
    assert _mode(target) == PRIVATE_FILE_MODE


def test_a_symlink_to_an_existing_database_leaves_its_mode_alone(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "target.db"
    target.parent.mkdir()
    open_db(target).close()
    target.chmod(0o644)
    link = tmp_path / "people.db"
    link.symlink_to(target)

    conn = open_db(link)
    conn.close()

    assert _mode(target) == 0o644
