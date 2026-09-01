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
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from people_context.adapters.filesystem.private_file import PRIVATE_FILE_MODE
from people_context.adapters.sqlite import UnsafeDatabasePathError, open_db
from people_context.adapters.sqlite.db import _resolve_target

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


def test_a_new_database_is_still_writable_under_an_owner_masking_umask(tmp_path: Path) -> None:
    """`mode` is a request the umask filters, including of owner bits.

    Under `0o200` the create alone yields `0o400`: not the documented mode, and read-only,
    so the very first migration write would fail on a database the process just made. The
    descriptor is therefore hardened explicitly rather than trusting what `os.open` left.
    """
    db_path = tmp_path / "masked.db"
    previous = os.umask(0o200)
    try:
        conn = open_db(db_path)
        try:
            conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
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


def test_the_database_opened_is_the_file_that_was_secured(tmp_path: Path) -> None:
    """Securing one name and connecting to another leaves a window between the two.

    A symlink repointed after the target is created but before SQLite connects would
    otherwise send the pages to a file that never received `0600`. Resolving once and
    using that single answer for both steps is what closes it, so this pins that the
    connect target is the resolved path rather than the name the caller passed.
    """
    target = tmp_path / "elsewhere" / "target.db"
    target.parent.mkdir()
    link = tmp_path / "people.db"
    link.symlink_to(target)

    resolved, is_memory, identity = _resolve_target(link)

    assert is_memory is False
    assert resolved == str(target.resolve())
    assert resolved != str(link)
    assert _mode(target) == PRIVATE_FILE_MODE
    # The identity is what lets the guard outlive the name it checked.
    stat = target.stat()
    assert identity == (stat.st_dev, stat.st_ino)


def test_resolving_the_in_memory_database_secures_nothing(tmp_path: Path) -> None:
    assert _resolve_target(":memory:") == (":memory:", True, None)
    assert list(tmp_path.iterdir()) == []


def test_a_symlink_into_a_missing_directory_defers_to_sqlite(tmp_path: Path) -> None:
    """Securing the file must not quietly invent directories the caller never named.

    `mkdir` covers the parent of the path that was passed; a link pointing somewhere
    else entirely is a different location, and creating it here would scatter empty
    directories on a typo. SQLite raises for the same path anyway, so the pre-creation
    step stands aside and lets that be the error the caller sees.
    """
    missing = tmp_path / "absent"
    link = tmp_path / "people.db"
    link.symlink_to(missing / "target.db")

    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        open_db(link)

    assert not missing.exists()


def test_a_symlink_raced_into_the_resolved_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`realpath` returns a name, and the name can be claimed before the open.

    In a directory another local account can write, that account can plant a symlink
    at the resolved path after resolution and before `O_CREAT | O_EXCL`. The open then
    reports `FileExistsError`, the path looks like an existing database, and SQLite
    follows the planted link and creates its target at the default `0o644` — so the
    pages end up in a file the attacker chose and can read.

    Resolution output is never legitimately a symlink, so one appearing here can only
    have arrived afterwards. The race is simulated deterministically by planting the
    link from inside `realpath`, which is exactly the window being closed.
    """
    victim = tmp_path / "people.db"
    attacker_target = tmp_path / "attacker.db"
    real_realpath = os.path.realpath

    def plant_symlink_during_resolution(path: object, *args: object, **kwargs: object) -> str:
        resolved = real_realpath(path, *args, **kwargs)  # type: ignore[arg-type]
        if not victim.is_symlink() and not victim.exists():
            victim.symlink_to(attacker_target)
        return resolved

    monkeypatch.setattr(os.path, "realpath", plant_symlink_during_resolution)

    with pytest.raises(UnsafeDatabasePathError, match="symlink appeared"):
        open_db(victim)

    assert not attacker_target.exists()


def test_an_ordinary_existing_database_is_still_not_refused(tmp_path: Path) -> None:
    """The refusal must key on the symlink, not merely on `FileExistsError`."""
    db_path = tmp_path / "people.db"
    open_db(db_path).close()
    db_path.chmod(0o644)

    conn = open_db(db_path)
    conn.close()

    assert _mode(db_path) == 0o644


def test_a_file_substituted_after_the_symlink_check_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`islink` checks a name, and the name can still change before the driver opens it.

    Where another local account owns the directory it can present an ordinary file for
    that check and swap in a symlink immediately after, so the guard passes while SQLite
    follows the replacement. Comparing the inode after the open catches the substitution
    whatever the name looked like in between; the swap is performed from inside
    `sqlite3.connect` here, which is precisely the window.
    """
    db_path = tmp_path / "people.db"
    attacker_target = tmp_path / "attacker.db"
    attacker_target.write_text("")
    real_connect = sqlite3.connect

    def swap_during_connect(target: object, *args: object, **kwargs: object) -> object:
        conn = real_connect(target, *args, **kwargs)  # type: ignore[arg-type]
        db_path.unlink()
        db_path.symlink_to(attacker_target)
        return conn

    monkeypatch.setattr(sqlite3, "connect", swap_during_connect)

    with pytest.raises(UnsafeDatabasePathError, match="stopped pointing at the file"):
        open_db(db_path)


def test_an_unswapped_open_passes_the_identity_check(tmp_path: Path) -> None:
    """The identity guard must not refuse the ordinary case it wraps."""
    db_path = tmp_path / "people.db"

    conn = open_db(db_path)
    try:
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    assert _mode(db_path) == PRIVATE_FILE_MODE

