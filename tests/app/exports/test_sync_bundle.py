"""Bootstrap sync-bundle export use-case tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from people_context.app.exports import ExportSyncBundle, render_bundle_json
from people_context.domain.sync_bundle import SYNC_BUNDLE_FORMAT, SYNC_BUNDLE_VERSION, SyncBundleDocument
from people_context.ports.changelog import ChangelogEntry
from people_context.ports.export import ExportSnapshot
from people_context.ports.hlc import HlcTimestamp
from people_context.ports.sync_bundle import BundleSource
from tests.app.fakes import FakeBundleReader, FakeClock

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
_RECORDED = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
_DEVICE_ID = "01J00000000000000000000DEV"
_PERSON_ID = "01J000000000000000000PERSON"


def _snapshot(**overrides: Any) -> ExportSnapshot:
    collections: dict[str, list[dict[str, Any]]] = {
        "people": [
            {
                "id": _PERSON_ID,
                "canonical_name": "Alice",
                "is_self": False,
                "summary": None,
                "aliases": [],
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": "2026-07-01T00:00:00Z",
                "deleted_at": None,
            }
        ],
        "organizations": [],
        "affiliations": [],
        "relationships": [],
        "facts": [],
        "observations": [],
        "traits": [],
        "interactions": [],
        "reminders": [],
        "user_preferences": [],
        "audit_log": [],
    }
    collections.update(overrides)
    return ExportSnapshot(**collections)


def _source(**overrides: Any) -> BundleSource:
    defaults: dict[str, Any] = {
        "origin_device_id": _DEVICE_ID,
        "watermark": HlcTimestamp(1_755_000_000_000, 3),
        "devices": [
            {
                "id": _DEVICE_ID,
                "display_name": "laptop",
                "public_key": None,
                "created_at": "2026-07-01T00:00:00+00:00",
                "retired_at": None,
                "hlc_physical_ms": 1_755_000_000_000,
                "hlc_logical": 3,
            }
        ],
        "snapshot": _snapshot(),
        "relationship_types": [
            {
                "type": "colleague_of",
                "inverse": None,
                "symmetric": True,
                "category": "professional",
                "canonical": True,
            }
        ],
        "relationship_synonyms": [{"synonym": "colleague", "type": "colleague_of"}],
        "changelog": [
            ChangelogEntry(
                op_id="01J000000000000000000OP001",
                device_id=_DEVICE_ID,
                hlc_physical_ms=1_755_000_000_000,
                hlc_logical=3,
                transaction_id="01J0000000000000000000TX01",
                entity_type="person",
                entity_id=_PERSON_ID,
                op_kind="create",
                payload={"zulu": 1, "alpha": 2},
                actor={"source": "user"},
                inserted_at=_RECORDED,
            )
        ],
    }
    defaults.update(overrides)
    return BundleSource(**defaults)


def test_export_builds_a_strict_version_one_envelope() -> None:
    reader = FakeBundleReader(_source())

    document = ExportSyncBundle(reader, FakeClock(_NOW)).execute()

    assert reader.calls == 1
    assert document.format == SYNC_BUNDLE_FORMAT
    assert document.version == SYNC_BUNDLE_VERSION
    assert document.created_at == _NOW
    assert document.origin_device_id == _DEVICE_ID
    assert (document.watermark.hlc_physical_ms, document.watermark.hlc_logical) == (1_755_000_000_000, 3)
    assert [person.canonical_name for person in document.snapshot.people] == ["Alice"]
    assert [row.type for row in document.relationship_vocabulary.types] == ["colleague_of"]
    assert [row.synonym for row in document.relationship_vocabulary.synonyms] == ["colleague"]
    assert [entry.op_id for entry in document.changelog] == ["01J000000000000000000OP001"]
    assert document.changelog[0].inserted_at == _RECORDED


def test_export_reads_the_snapshot_exactly_once() -> None:
    reader = FakeBundleReader(_source())
    use_case = ExportSyncBundle(reader, FakeClock(_NOW))

    use_case.execute()
    use_case.execute()

    assert reader.calls == 2


def test_rendered_bundle_is_byte_identical_for_one_snapshot_and_clock() -> None:
    use_case = ExportSyncBundle(FakeBundleReader(_source()), FakeClock(_NOW))

    first = render_bundle_json(use_case.execute())
    second = render_bundle_json(use_case.execute())

    assert first == second
    assert first.endswith("\n")


def test_rendered_bundle_is_canonical_regardless_of_opaque_payload_key_order() -> None:
    reordered = _source()
    reordered.changelog[0].payload = {"alpha": 2, "zulu": 1}

    ordered_first = render_bundle_json(ExportSyncBundle(FakeBundleReader(_source()), FakeClock(_NOW)).execute())
    ordered_second = render_bundle_json(ExportSyncBundle(FakeBundleReader(reordered), FakeClock(_NOW)).execute())

    assert ordered_first == ordered_second


def test_rendered_bundle_round_trips_through_the_strict_document() -> None:
    document = ExportSyncBundle(FakeBundleReader(_source()), FakeClock(_NOW)).execute()

    text = render_bundle_json(document)
    parsed = SyncBundleDocument.model_validate(json.loads(text))

    assert parsed == document


def test_export_rejects_a_snapshot_row_that_violates_the_strict_contract() -> None:
    broken = _source(snapshot=_snapshot(people=[{"id": "", "canonical_name": "Blank"}]))

    with pytest.raises(ValidationError):
        ExportSyncBundle(FakeBundleReader(broken), FakeClock(_NOW)).execute()
