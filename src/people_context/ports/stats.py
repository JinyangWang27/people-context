"""Read-side port for the aggregate-only local store inventory.

Everything crossing this boundary is a count or a byte total. The reader never returns a
canonical name, a fact value, an interaction summary, a device display name, or a filesystem
path, so no amount of misuse downstream can turn a stats report into a disclosure of stored
personal data. Ordering, redaction, and presentation are application policy, which is why the
distributions cross as plain mappings rather than as already-sorted rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Tables whose row counts the report documents, in the order the report lists them.
#: Adding a table here is an additive change to the stats document; removing one is not.
DOCUMENTED_TABLES: tuple[str, ...] = (
    "persons",
    "aliases",
    "organizations",
    "affiliations",
    "relationships",
    "facts",
    "observations",
    "traits",
    "interactions",
    "interaction_participants",
    "reminders",
    "user_preferences",
    "import_staging",
    "audit_log",
    "devices",
    "changelog",
    "sync_conflicts",
    "relationship_types",
    "relationship_type_synonyms",
)

#: Bucket used for a stored relationship whose type has no vocabulary row, matching the
#: sentinel the recency reader already groups uncategorized relationships under.
UNCATEGORIZED_RELATIONSHIP = "uncategorized"

#: `storage_kind` values. `file` means the byte totals were measured; the other two are
#: explicit "there is nothing to measure" states so a reader never mistakes an unmeasurable
#: database for an empty one.
STORAGE_FILE = "file"
STORAGE_MEMORY = "memory"
STORAGE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StorageFootprint:
    """On-disk bytes used by the database and its WAL companions.

    WAL mode keeps recently written pages outside the main file, so the main file alone can
    understate the real footprint by an arbitrary amount. The companions are therefore
    measured separately and summed, and a companion that does not exist contributes zero
    rather than making the total unknown.

    Every byte count is `None` when `storage_kind` is not `file`, which distinguishes "this
    database has no measurable file" from "this database occupies no space".
    """

    storage_kind: str
    database_bytes: int | None = None
    main_bytes: int | None = None
    wal_bytes: int | None = None
    shm_bytes: int | None = None


@dataclass(frozen=True)
class StoreInventory:
    """One aggregate snapshot of the local store.

    People are counted by lifecycle state rather than as a single table total: a soft-deleted
    person still occupies a row, so an undifferentiated `persons` count would misreport how
    many people the store actually knows about.
    """

    active_people: int = 0
    soft_deleted_people: int = 0
    self_people: int = 0
    table_rows: dict[str, int] = field(default_factory=dict)
    alias_kinds: dict[str, int] = field(default_factory=dict)
    fact_sensitivity: dict[str, int] = field(default_factory=dict)
    observation_sensitivity: dict[str, int] = field(default_factory=dict)
    relationship_categories: dict[str, int] = field(default_factory=dict)
    audit_operations: dict[str, int] = field(default_factory=dict)
    changelog_devices: dict[str, int] = field(default_factory=dict)
    storage: StorageFootprint = field(default_factory=lambda: StorageFootprint(STORAGE_UNAVAILABLE))


@runtime_checkable
class StatsReader(Protocol):
    """Read aggregate counts and storage bytes without reading record content."""

    def read_inventory(self) -> StoreInventory: ...
