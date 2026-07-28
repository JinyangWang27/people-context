"""Single-snapshot bundle read port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from people_context.ports.changelog import ChangelogEntry
from people_context.ports.export import ExportSnapshot
from people_context.ports.hlc import HlcTimestamp


@dataclass(frozen=True)
class BundleSource:
    """Everything one bootstrap bundle needs, read from a single database snapshot.

    Rows are plain JSON-compatible mappings in the established export shape; the
    application layer validates them into the strict versioned bundle document.
    """

    origin_device_id: str
    watermark: HlcTimestamp
    devices: list[dict[str, Any]]
    snapshot: ExportSnapshot
    relationship_types: list[dict[str, Any]]
    relationship_synonyms: list[dict[str, Any]]
    changelog: list[ChangelogEntry]


@runtime_checkable
class BundleReader(Protocol):
    """Read one complete, point-in-time bundle source in a single transaction."""

    def read_bundle(self) -> BundleSource: ...
