"""Ports for reading and writing an Obsidian relationship vault."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from people_context.domain.vault import VaultSnapshot


class VaultSafetyError(RuntimeError):
    """Raised when an output path cannot be proven safe for regeneration."""


@runtime_checkable
class VaultReader(Protocol):
    """Read the bounded records included in the vault export contract."""

    def read_vault(self, *, include_sensitive: bool = False) -> VaultSnapshot: ...


@runtime_checkable
class VaultWriter(Protocol):
    """Replace an owned output directory with deterministic vault files."""

    def write_vault(self, output: Path, snapshot: VaultSnapshot) -> list[Path]: ...
