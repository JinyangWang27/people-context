"""Verbatim bootstrap restore port.

Restore is the single documented exception to the ordinary ``audit_mutation`` seam: it
reinstates original ids, timestamps, provenance, audit rows, and changelog rows exactly as
the origin device recorded them, and therefore must not mint new accountability or replay
history of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from people_context.domain.sync_bundle import SyncBundleDocument
from people_context.ports.hlc import HlcTimestamp


@dataclass(frozen=True)
class RestoreOutcome:
    """Row counts written by one successful restore, plus the resulting local clock."""

    people: int
    organizations: int
    affiliations: int
    relationships: int
    facts: int
    observations: int
    traits: int
    interactions: int
    reminders: int
    user_preferences: int
    audit_entries: int
    relationship_types: int
    relationship_synonyms: int
    devices: int
    changelog_entries: int
    indexed_names: int
    local_watermark: HlcTimestamp


@runtime_checkable
class BootstrapRestorer(Protocol):
    """Restore one validated bundle into a baseline-empty destination, or refuse."""

    def restore(self, document: SyncBundleDocument) -> RestoreOutcome: ...
