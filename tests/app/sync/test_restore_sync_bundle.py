"""Bootstrap restore use-case tests against a fake restorer port."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.app.sync import RestoreSyncBundle
from people_context.domain.sync_bundle import (
    SYNC_BUNDLE_FORMAT,
    SYNC_BUNDLE_VERSION,
    InvalidBundleError,
    SyncBundleDocument,
    TargetNotEmptyError,
)
from people_context.ports.bootstrap_restore import RestoreOutcome
from people_context.ports.hlc import HlcTimestamp

_DEVICE_ID = "01J00000000000000000000DEV"
_PERSON_ID = "01J000000000000000000PERSON"


def _payload() -> dict[str, Any]:
    return {
        "format": SYNC_BUNDLE_FORMAT,
        "version": SYNC_BUNDLE_VERSION,
        "created_at": "2026-07-19T12:00:00Z",
        "origin_device_id": _DEVICE_ID,
        "watermark": {"hlc_physical_ms": 1755000000000, "hlc_logical": 3},
        "devices": [
            {
                "id": _DEVICE_ID,
                "display_name": "laptop",
                "public_key": None,
                "created_at": "2026-07-01T00:00:00Z",
                "retired_at": None,
                "hlc_physical_ms": 1755000000000,
                "hlc_logical": 3,
            }
        ],
        "snapshot": {
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
        },
        "relationship_vocabulary": {"types": [], "synonyms": []},
        "changelog": [],
        "imports": {"source_sessions": [], "candidate_mappings": [], "staging": []},
    }


class _FakeRestorer:
    """Record what the use case hands the port, or refuse on demand."""

    def __init__(self, refusal: Exception | None = None) -> None:
        self.refusal = refusal
        self.restored: list[SyncBundleDocument] = []

    def restore(self, document: SyncBundleDocument) -> RestoreOutcome:
        if self.refusal is not None:
            raise self.refusal
        self.restored.append(document)
        return RestoreOutcome(
            people=len(document.snapshot.people),
            organizations=0,
            affiliations=0,
            relationships=0,
            facts=0,
            observations=0,
            traits=0,
            interactions=0,
            reminders=0,
            user_preferences=0,
            audit_entries=0,
            relationship_types=0,
            relationship_synonyms=0,
            devices=len(document.devices),
            changelog_entries=len(document.changelog),
            indexed_names=1,
            local_watermark=HlcTimestamp(1755000000001, 0),
        )


def test_parse_returns_a_validated_document() -> None:
    use_case = RestoreSyncBundle(_FakeRestorer())

    document = use_case.parse(json.dumps(_payload()))

    assert document.origin_device_id == _DEVICE_ID
    assert document.created_at == datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    ["", "not json", "[]", '{"format": "people-context-sync-bundle"}'],
)
def test_unparseable_or_incomplete_text_is_refused(text: str) -> None:
    use_case = RestoreSyncBundle(_FakeRestorer())

    with pytest.raises(InvalidBundleError) as error:
        use_case.parse(text)

    assert error.value.code == "invalid_bundle"
    assert error.value.details


def test_structural_details_name_locations_without_echoing_input() -> None:
    payload = _payload()
    payload["format"] = "people-context-export"
    payload["snapshot"]["people"][0]["canonical_name"] = "Extremely Private Name"
    payload["unexpected"] = "value"

    use_case = RestoreSyncBundle(_FakeRestorer())
    with pytest.raises(InvalidBundleError) as error:
        use_case.parse(json.dumps(payload))

    joined = " ".join(error.value.details)
    assert "format" in joined
    assert "unexpected" in joined
    assert "Extremely Private Name" not in joined
    assert "people-context-export" not in joined


def test_cross_field_violations_are_refused_before_any_restore() -> None:
    payload = _payload()
    payload["origin_device_id"] = "01J0000000000000000000DEV2"
    restorer = _FakeRestorer()
    use_case = RestoreSyncBundle(restorer)

    with pytest.raises(InvalidBundleError):
        use_case.parse(json.dumps(payload))

    assert restorer.restored == []


def test_preview_summarizes_without_touching_the_destination() -> None:
    restorer = _FakeRestorer()
    use_case = RestoreSyncBundle(restorer)
    document = use_case.parse(json.dumps(_payload()))

    preview = use_case.preview(document)

    assert preview.origin_device_id == _DEVICE_ID
    assert preview.watermark == (1755000000000, 3)
    assert preview.counts["people"] == 1
    assert preview.counts["changelog entries"] == 0
    assert set(preview.counts) >= {"people", "audit entries", "devices", "relationship types"}
    assert restorer.restored == []


def test_execute_delegates_the_validated_document_to_the_port() -> None:
    restorer = _FakeRestorer()
    use_case = RestoreSyncBundle(restorer)
    document = use_case.parse(json.dumps(_payload()))

    outcome = use_case.execute(document)

    assert restorer.restored == [document]
    assert outcome.people == 1
    assert outcome.local_watermark == HlcTimestamp(1755000000001, 0)


def test_a_port_refusal_propagates_unchanged() -> None:
    refusal = TargetNotEmptyError(["persons: 3 row(s)"])
    use_case = RestoreSyncBundle(_FakeRestorer(refusal))
    document = use_case.parse(json.dumps(_payload()))

    with pytest.raises(TargetNotEmptyError) as error:
        use_case.execute(document)

    assert error.value.code == "target_not_empty"
    assert error.value.details == ("persons: 3 row(s)",)
