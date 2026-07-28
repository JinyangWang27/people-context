"""Filesystem output adapters."""

from people_context.adapters.filesystem.private_file import (
    PRIVATE_FILE_MODE,
    atomic_write_private_text,
)
from people_context.adapters.filesystem.vault_writer import (
    MARKER_FILE,
    FileSystemVaultWriter,
    sanitize_filename,
)

__all__ = [
    "MARKER_FILE",
    "PRIVATE_FILE_MODE",
    "FileSystemVaultWriter",
    "atomic_write_private_text",
    "sanitize_filename",
]
