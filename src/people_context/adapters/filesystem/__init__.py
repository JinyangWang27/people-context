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
from people_context.adapters.filesystem.vcard_writer import CanonicalVCardWriter

__all__ = [
    "MARKER_FILE",
    "PRIVATE_FILE_MODE",
    "CanonicalVCardWriter",
    "FileSystemVaultWriter",
    "atomic_write_private_text",
    "sanitize_filename",
]
