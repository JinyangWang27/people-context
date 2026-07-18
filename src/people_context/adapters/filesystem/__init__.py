"""Filesystem output adapters."""

from people_context.adapters.filesystem.vault_writer import (
    MARKER_FILE,
    FileSystemVaultWriter,
    sanitize_filename,
)

__all__ = ["FileSystemVaultWriter", "MARKER_FILE", "sanitize_filename"]
