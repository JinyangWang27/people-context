"""Application policy for the aggregate-only stats report."""

from __future__ import annotations

import json

import pytest

from people_context.app.context import (
    STATS_FORMAT,
    STATS_VERSION,
    ReportStoreStats,
    render_stats_json,
)
from people_context.config import EXPORT_ENV, SENSITIVE_CONTEXT_ENV
from people_context.ports.stats import (
    DOCUMENTED_TABLES,
    STORAGE_FILE,
    STORAGE_MEMORY,
    StorageFootprint,
    StoreInventory,
)
from tests.app.fakes import FakeClock, FakeStatsReader


def _inventory(**overrides: object) -> StoreInventory:
    defaults: dict[str, object] = {
        "active_people": 3,
        "soft_deleted_people": 1,
        "self_people": 1,
        "table_rows": {"persons": 4, "facts": 2},
        "alias_kinds": {"handle": 2, "nickname": 5},
        "fact_sensitivity": {"personal": 2},
        "observation_sensitivity": {},
        "relationship_categories": {"social": 1},
        "audit_operations": {"create": 4},
        "changelog_devices": {"01DEVICE": 6},
        "storage": StorageFootprint(
            storage_kind=STORAGE_FILE,
            database_bytes=300,
            main_bytes=100,
            wal_bytes=150,
            shm_bytes=50,
        ),
    }
    defaults.update(overrides)
    return StoreInventory(**defaults)  # type: ignore[arg-type]


def _use_case(inventory: StoreInventory | None = None) -> tuple[ReportStoreStats, FakeStatsReader]:
    reader = FakeStatsReader(inventory or _inventory())
    return ReportStoreStats(reader, FakeClock()), reader


def test_the_document_is_versioned_and_declares_its_format() -> None:
    use_case, _ = _use_case()

    report = use_case.execute()

    assert report.format == STATS_FORMAT
    assert report.version == STATS_VERSION
    assert report.generated_at == FakeClock().now()


def test_the_resolved_path_is_redacted_unless_the_operator_asks_for_it() -> None:
    use_case, _ = _use_case()

    redacted = use_case.execute(database_path="/home/ada/.local/share/people-context/people.db")
    included = use_case.execute(
        database_path="/home/ada/.local/share/people-context/people.db",
        include_path=True,
    )

    assert redacted.database_path is None
    assert "ada" not in render_stats_json(redacted)
    assert included.database_path == "/home/ada/.local/share/people-context/people.db"


def test_gate_booleans_are_reported_exactly_as_the_caller_passed_them() -> None:
    use_case, _ = _use_case()

    off = use_case.execute()
    on = use_case.execute(sensitive_context_enabled=True, export_enabled=True)

    assert (off.environment.sensitive_context, off.environment.export) == (False, False)
    assert (on.environment.sensitive_context, on.environment.export) == (True, True)


def test_the_use_case_never_reads_the_environment_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate state is the caller's to establish at the process boundary and pass in explicitly."""
    use_case, _ = _use_case()
    monkeypatch.setenv(SENSITIVE_CONTEXT_ENV, "1")
    monkeypatch.setenv(EXPORT_ENV, "1")

    report = use_case.execute(sensitive_context_enabled=False, export_enabled=False)

    assert report.environment.sensitive_context is False
    assert report.environment.export is False


def test_every_documented_table_appears_even_when_the_reader_omitted_it() -> None:
    """A table missing from the list would be indistinguishable from one holding no rows."""
    use_case, _ = _use_case(_inventory(table_rows={"persons": 4}))

    report = use_case.execute()

    assert [entry.key for entry in report.tables] == list(DOCUMENTED_TABLES)
    counts = {entry.key: entry.count for entry in report.tables}
    assert counts["persons"] == 4
    assert counts["facts"] == 0


def test_people_are_split_by_lifecycle_state() -> None:
    use_case, _ = _use_case()

    report = use_case.execute()

    assert report.people.active == 3
    assert report.people.soft_deleted == 1
    assert report.people.self_records == 1


def test_distributions_are_ordered_largest_first_then_by_key() -> None:
    use_case, _ = _use_case(_inventory(alias_kinds={"other": 4, "handle": 9, "native_script": 4, "nickname": 1}))

    report = use_case.execute()

    assert [(entry.key, entry.count) for entry in report.alias_kinds] == [
        ("handle", 9),
        ("native_script", 4),
        ("other", 4),
        ("nickname", 1),
    ]


def test_an_empty_distribution_stays_an_empty_list() -> None:
    use_case, _ = _use_case()

    report = use_case.execute()

    assert report.observation_sensitivity == []


def test_storage_carries_the_components_and_their_sum() -> None:
    use_case, _ = _use_case()

    storage = use_case.execute().storage

    assert storage.storage_kind == STORAGE_FILE
    assert storage.database_bytes == 300
    assert (storage.main_bytes, storage.wal_bytes, storage.shm_bytes) == (100, 150, 50)


def test_an_unmeasurable_footprint_stays_null_rather_than_becoming_zero() -> None:
    use_case, _ = _use_case(_inventory(storage=StorageFootprint(STORAGE_MEMORY)))

    storage = use_case.execute().storage

    assert storage.storage_kind == STORAGE_MEMORY
    assert storage.database_bytes is None
    assert storage.main_bytes is None
    assert '"database_bytes": null' in render_stats_json(use_case.execute())


def test_the_report_holds_no_record_text_only_counts_and_bucket_names() -> None:
    use_case, _ = _use_case()

    document = json.loads(render_stats_json(use_case.execute()))

    for section in (
        "tables",
        "alias_kinds",
        "fact_sensitivity",
        "observation_sensitivity",
        "relationship_categories",
        "audit_operations",
        "changelog_devices",
    ):
        for entry in document[section]:
            assert set(entry) == {"key", "count"}
            assert isinstance(entry["count"], int)


def test_the_document_shape_is_stable_across_releases() -> None:
    """The declared top-level keys are the contract; a later release may only add to them."""
    use_case, _ = _use_case()

    document = json.loads(render_stats_json(use_case.execute()))

    assert {
        "format",
        "version",
        "generated_at",
        "database_path",
        "people",
        "tables",
        "alias_kinds",
        "fact_sensitivity",
        "observation_sensitivity",
        "relationship_categories",
        "audit_operations",
        "changelog_devices",
        "storage",
        "environment",
    } <= set(document)
    assert set(document["storage"]) >= {
        "storage_kind",
        "database_bytes",
        "main_bytes",
        "wal_bytes",
        "shm_bytes",
    }
    assert set(document["environment"]) >= {"sensitive_context", "export"}


def test_the_use_case_reads_the_inventory_once_per_report() -> None:
    use_case, reader = _use_case()

    use_case.execute()

    assert reader.calls == 1


def test_rendering_the_same_report_twice_produces_identical_bytes() -> None:
    use_case, _ = _use_case()

    report = use_case.execute()

    assert render_stats_json(report) == render_stats_json(report)
    assert render_stats_json(report).endswith("\n")
