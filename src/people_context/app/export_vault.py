"""Application orchestration for the CLI-only Obsidian vault export."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from people_context.ports.vault import VaultReader, VaultWriter


class ExportVaultResult(BaseModel):
    """Summary of one successful deterministic vault regeneration."""

    output: Path
    files: int
    people: int
    organizations: int


class ExportVault:
    """Read the safe export projection and delegate filesystem generation."""

    def __init__(self, reader: VaultReader, writer: VaultWriter) -> None:
        self._reader = reader
        self._writer = writer

    def execute(self, output: str | Path, *, include_sensitive: bool = False) -> ExportVaultResult:
        snapshot = self._reader.read_vault(include_sensitive=include_sensitive)
        target = Path(output).expanduser()
        files = self._writer.write_vault(target, snapshot)
        return ExportVaultResult(
            output=target,
            files=len(files),
            people=len(snapshot.people),
            organizations=len(snapshot.organizations),
        )
