"""Atomic owner-private text file publication for personal-data exports."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

PRIVATE_FILE_MODE = 0o600
_TEMP_PREFIX = ".people-context-"
_TEMP_SUFFIX = ".tmp"


def atomic_write_private_text(path: str | Path, text: str) -> Path:
    """Publish ``text`` at ``path`` as an owner-only file, atomically.

    Personal data must never be visible to other local accounts, not even briefly, and a
    failed export must never destroy a previously valid file. The content is therefore
    written to a unique ``O_CREAT | O_EXCL`` temporary file with mode ``0o600`` in the
    destination directory, flushed and ``fsync``ed, and only then moved into place with
    ``os.replace``.

    Passing ``0o600`` to ``os.open(..., O_TRUNC)`` is not sufficient, because an existing
    permissive destination keeps its old mode. Replacing the directory entry also means a
    destination symlink is replaced rather than followed, so an unexpected target outside
    the destination directory is never truncated or overwritten.
    """
    destination = Path(path)
    directory = destination.parent
    handle, temp_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX, dir=directory)
    temp_path = Path(temp_name)
    published = False
    try:
        try:
            stream = os.fdopen(handle, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(handle)
            raise
        with stream:
            restrict_fd_to_owner(stream.fileno())
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, destination)
        published = True
    finally:
        # Only an unpublished temporary file is ours to remove; after the rename the
        # same inode is the destination, so unlinking it would delete the export.
        if not published:
            with suppress(OSError):
                os.unlink(temp_path)
    _fsync_directory(directory)
    return destination


def restrict_fd_to_owner(fd: int) -> None:
    """Force owner-only permissions on an open descriptor.

    `os.open`'s `mode` is only a request: the process umask masks it, and a umask
    that clears an owner bit (`0o200`, say) yields `0o400` rather than the `0o600`
    intended. Setting the mode explicitly on the descriptor restores exactly the
    requested permissions, and doing it through the descriptor rather than the
    path means no window where the name resolves to something else.
    """
    if hasattr(os, "fchmod"):
        with suppress(OSError, NotImplementedError):
            os.fchmod(fd, PRIVATE_FILE_MODE)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself where the platform supports directory fsync."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
