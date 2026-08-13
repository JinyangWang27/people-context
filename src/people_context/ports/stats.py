"""Read-side port for the aggregate-only local store inventory.

Everything crossing this boundary is a count or a byte total. The reader never returns a
canonical name, a fact value, an interaction summary, a device display name, or a filesystem
path, so no amount of misuse downstream can turn a stats report into a disclosure of stored
personal data. Ordering, redaction, and presentation are application policy, which is why the
distributions cross as plain mappings rather than as already-sorted rows.

That guarantee holds for the grouping keys too, and two of them need help to keep it. A
relationship category and a restored audit operation are strings this project did not choose:
the first is typed by the operator, the second comes from whatever installation produced the
bundle. Both are folded into the sentinels below before they cross, so a bucket name is always
either schema vocabulary or a sentinel — never authored text.

Device ids are the third such key and the one that cannot simply be collapsed, because telling
devices apart is the whole point of counting per device. A locally minted id is opaque and
names nobody, so it crosses as itself; one that is not shaped like a generated id — which
restore permits, since a bundle carries whatever its origin wrote — keeps a bucket of its own
under a positional pseudonym instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from people_context.domain.relationship_vocabulary import SEEDED_RELATIONSHIP_TYPES

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

#: Bucket used for a relationship whose category is not one this release seeds. A category is
#: free text the operator typed at `pctx relationship-types add --category`, so emitting it
#: verbatim would put an authored string — potentially a personal one — into a report whose
#: whole promise is that it carries none. Counting those relationships under one sentinel keeps
#: the distribution's total honest without disclosing what the operator called them.
CUSTOM_RELATIONSHIP_CATEGORY = "custom"

#: Categories this release seeds, derived from the vocabulary itself so the two cannot drift.
SEEDED_RELATIONSHIP_CATEGORIES: frozenset[str] = frozenset(
    row.category for row in SEEDED_RELATIONSHIP_TYPES.values()
)

#: Bucket used for an audit operation outside `KNOWN_AUDIT_OPERATIONS`. Restore carries an
#: origin's audit rows verbatim, and their `op` is only as constrained as that origin was, so
#: an unrecognized operation is counted rather than named.
OTHER_AUDIT_OPERATION = "other"

#: Prefix for a device whose id is not shaped like one this project generates. Restore accepts
#: any non-blank device id, so a bundle can carry a hostname or a personal label where an
#: opaque key belongs. Such a device keeps its own bucket — per-device counts are the point of
#: the distribution — but under a positional pseudonym rather than the string someone chose.
#: Pseudonyms are assigned in sorted id order, so the same store always numbers them the same.
UNRECOGNIZED_DEVICE_PREFIX = "unrecognized-device-"

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
