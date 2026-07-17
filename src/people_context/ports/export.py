"""Portable export read port and plain snapshot DTO."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ExportSnapshot:
    """All persisted domain collections, excluding implementation tables."""

    people: list[dict[str, Any]]
    organizations: list[dict[str, Any]]
    affiliations: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    traits: list[dict[str, Any]]
    interactions: list[dict[str, Any]]
    reminders: list[dict[str, Any]]
    user_preferences: list[dict[str, Any]]
    audit_log: list[dict[str, Any]]


@runtime_checkable
class ExportReader(Protocol):
    """Read a deterministic complete portable snapshot."""

    def read_export(self) -> ExportSnapshot: ...
