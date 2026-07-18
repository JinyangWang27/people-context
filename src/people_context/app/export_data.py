"""Full portable dataset export."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from people_context.ports.clock import Clock
from people_context.ports.export import ExportReader


class ExportDocument(BaseModel):
    """Versioned domain-shaped portable export envelope; M6 intentionally excludes the changelog."""

    format: str = "people-context-export"
    version: int = 1
    exported_at: datetime
    people: list[dict[str, Any]] = Field(default_factory=list)
    organizations: list[dict[str, Any]] = Field(default_factory=list)
    affiliations: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    traits: list[dict[str, Any]] = Field(default_factory=list)
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    reminders: list[dict[str, Any]] = Field(default_factory=list)
    user_preferences: list[dict[str, Any]] = Field(default_factory=list)
    audit_log: list[dict[str, Any]] = Field(default_factory=list)


class ExportData:
    """Build the M3-compatible snapshot export; M7 will define changelog/bootstrap portability."""

    def __init__(self, reader: ExportReader, clock: Clock) -> None:
        self._reader = reader
        self._clock = clock

    def execute(self) -> ExportDocument:
        snapshot = self._reader.read_export()
        return ExportDocument(exported_at=self._clock.now(), **snapshot.__dict__)
