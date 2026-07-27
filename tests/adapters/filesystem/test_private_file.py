"""Atomic owner-private file writer tests."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from people_context.adapters.filesystem.private_file import atomic_write_private_text

_PERMISSIVE_FIXTURE_MODE = 0o640


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _temporary_leftovers(directory: Path) -> list[Path]:
    return [child for child in directory.iterdir() if child.name.startswith(".people-context-")]


def test_new_file_is_written_owner_only(tmp_path: Path) -> None:
    destination = tmp_path / "bundle.json"

    result = atomic_write_private_text(destination, "payload\n")

    assert result == destination
    assert destination.read_text(encoding="utf-8") == "payload\n"
    assert _mode(destination) == 0o600
    assert _temporary_leftovers(tmp_path) == []


def test_pre_existing_group_readable_file_becomes_owner_only(tmp_path: Path) -> None:
    destination = tmp_path / "bundle.json"
    destination.write_text("stale\n", encoding="utf-8")
    # CodeQL `security-extended` rejects creating a world-readable file even in a fixture, so the
    # pre-existing permissive mode is group-readable. It proves the same defect: an `O_TRUNC` write
    # would retain whatever non-owner bits the destination already had.
    os.chmod(destination, _PERMISSIVE_FIXTURE_MODE)

    atomic_write_private_text(destination, "fresh\n")

    assert destination.read_text(encoding="utf-8") == "fresh\n"
    assert _mode(destination) == 0o600


def test_destination_symlink_is_replaced_without_touching_its_target(tmp_path: Path) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("do not touch\n", encoding="utf-8")
    os.chmod(target, _PERMISSIVE_FIXTURE_MODE)
    destination = tmp_path / "bundle.json"
    destination.symlink_to(target)

    atomic_write_private_text(destination, "fresh\n")

    assert not destination.is_symlink()
    assert destination.read_text(encoding="utf-8") == "fresh\n"
    assert _mode(destination) == 0o600
    assert target.read_text(encoding="utf-8") == "do not touch\n"
    assert _mode(target) == _PERMISSIVE_FIXTURE_MODE


def test_failed_publication_preserves_the_existing_file_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "bundle.json"
    destination.write_text("previous\n", encoding="utf-8")
    os.chmod(destination, 0o600)

    def _fail(source: object, target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", _fail)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_private_text(destination, "fresh\n")

    assert destination.read_text(encoding="utf-8") == "previous\n"
    assert _mode(destination) == 0o600
    assert _temporary_leftovers(tmp_path) == []


def test_failed_write_leaves_no_destination_and_no_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "bundle.json"

    def _fail(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", _fail)

    with pytest.raises(OSError, match="fsync failed"):
        atomic_write_private_text(destination, "fresh\n")

    assert not destination.exists()
    assert _temporary_leftovers(tmp_path) == []


def test_bare_relative_destination_is_written_in_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    atomic_write_private_text("bundle.json", "relative\n")

    assert (tmp_path / "bundle.json").read_text(encoding="utf-8") == "relative\n"
    assert _mode(tmp_path / "bundle.json") == 0o600
