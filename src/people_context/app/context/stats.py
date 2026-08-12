"""Aggregate-only inventory of the local store, with the path redacted by default.

The report answers "how much is in here" without answering "what is in here". Nothing it
carries is a stored personal value: every figure is a count, a byte total, or a
closed-vocabulary bucket name that the schema itself defines.

Two things are still the application's to decide. The resolved database path is a real
disclosure — it usually contains the operator's account name, and it says where the file
lives — so it is omitted unless the operator explicitly asks for it. The MCP elevation gates
are reported as facts about *this* process's environment, read by the caller from its own
environment and passed in explicitly; neither this use case nor its reader starts, contacts,
or probes a server to find out.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.ports.clock import Clock
from people_context.ports.stats import DOCUMENTED_TABLES, StatsReader

STATS_FORMAT = "people-context-stats"
STATS_VERSION = 1


class CountEntry(BaseModel):
    """One bucket of a distribution."""

    key: str
    count: int


class PeopleCounts(BaseModel):
    """People by lifecycle state, because a soft-deleted person still occupies a row."""

    active: int = 0
    soft_deleted: int = 0
    self_records: int = 0


class StorageUsage(BaseModel):
    """Bytes occupied by the database file and its WAL companions.

    `database_bytes` is the sum of the three components. Every figure is `null` when
    `storage_kind` is not `file`, which says the footprint is unmeasurable rather than zero.
    """

    storage_kind: str
    database_bytes: int | None = None
    main_bytes: int | None = None
    wal_bytes: int | None = None
    shm_bytes: int | None = None


class EnvironmentGates(BaseModel):
    """Whether elevated MCP capabilities are enabled in the calling process's environment.

    This describes the environment the report was produced in, not the configuration of any
    running server: a server started from a different environment has different gates.
    """

    sensitive_context: bool = False
    export: bool = False


class StatsReport(BaseModel):
    """The versioned stats document; a declared machine interface under the M12 promise."""

    format: str = STATS_FORMAT
    version: int = STATS_VERSION
    generated_at: datetime
    database_path: str | None = None
    people: PeopleCounts = Field(default_factory=PeopleCounts)
    tables: list[CountEntry] = Field(default_factory=list)
    alias_kinds: list[CountEntry] = Field(default_factory=list)
    fact_sensitivity: list[CountEntry] = Field(default_factory=list)
    observation_sensitivity: list[CountEntry] = Field(default_factory=list)
    relationship_categories: list[CountEntry] = Field(default_factory=list)
    audit_operations: list[CountEntry] = Field(default_factory=list)
    changelog_devices: list[CountEntry] = Field(default_factory=list)
    storage: StorageUsage
    environment: EnvironmentGates = Field(default_factory=EnvironmentGates)


class ReportStoreStats:
    """Turn one aggregate store inventory into a deterministic, redacted document."""

    def __init__(self, stats: StatsReader, clock: Clock) -> None:
        self._stats = stats
        self._clock = clock

    def execute(
        self,
        *,
        sensitive_context_enabled: bool = False,
        export_enabled: bool = False,
        database_path: str | None = None,
        include_path: bool = False,
    ) -> StatsReport:
        """Return the inventory, including the path only when the operator asked for it."""
        inventory = self._stats.read_inventory()
        return StatsReport(
            generated_at=self._clock.now(),
            database_path=database_path if include_path else None,
            people=PeopleCounts(
                active=inventory.active_people,
                soft_deleted=inventory.soft_deleted_people,
                self_records=inventory.self_people,
            ),
            # Every documented table appears, including the ones holding nothing: a table
            # missing from the list would be indistinguishable from a table with no rows.
            tables=[
                CountEntry(key=table, count=inventory.table_rows.get(table, 0)) for table in DOCUMENTED_TABLES
            ],
            alias_kinds=_distribution(inventory.alias_kinds),
            fact_sensitivity=_distribution(inventory.fact_sensitivity),
            observation_sensitivity=_distribution(inventory.observation_sensitivity),
            relationship_categories=_distribution(inventory.relationship_categories),
            audit_operations=_distribution(inventory.audit_operations),
            changelog_devices=_distribution(inventory.changelog_devices),
            storage=StorageUsage(
                storage_kind=inventory.storage.storage_kind,
                database_bytes=inventory.storage.database_bytes,
                main_bytes=inventory.storage.main_bytes,
                wal_bytes=inventory.storage.wal_bytes,
                shm_bytes=inventory.storage.shm_bytes,
            ),
            environment=EnvironmentGates(
                sensitive_context=sensitive_context_enabled,
                export=export_enabled,
            ),
        )


def render_stats_json(report: StatsReport) -> str:
    """Render the versioned machine document as canonical JSON text."""
    return render_json_document(report)


def _distribution(counts: Mapping[str, int]) -> list[CountEntry]:
    """Order a distribution largest-first, breaking ties by key.

    The key tiebreak is what makes the order total: without it two equally sized buckets
    could swap places between runs over unchanged data, purely on the storage engine's
    grouping order.
    """
    return [
        CountEntry(key=key, count=counts[key])
        for key in sorted(counts, key=lambda key: (-counts[key], key))
    ]
