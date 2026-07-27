"""Strict bootstrap sync-bundle contract tests."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from people_context.domain import sync_bundle
from people_context.domain.sync_bundle import (
    SYNC_BUNDLE_FORMAT,
    SYNC_BUNDLE_VERSION,
    StrictBundleModel,
    SyncBundleDocument,
)

_DEVICE_ID = "01J00000000000000000000DEV"
_PERSON_ID = "01J000000000000000000PERSON"


def _document() -> dict[str, Any]:
    """Return one complete, structurally valid version-1 bundle document."""
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
                    "aliases": [
                        {
                            "id": "01J0000000000000000ALIAS01",
                            "value": "Al",
                            "kind": "nickname",
                            "lang": None,
                            "script": None,
                        }
                    ],
                    "created_at": "2026-07-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "deleted_at": None,
                }
            ],
            "organizations": [],
            "affiliations": [],
            "relationships": [],
            "facts": [
                {
                    "id": "01J00000000000000000FACT01",
                    "person_id": _PERSON_ID,
                    "predicate": "city",
                    "value": "Berlin",
                    "period": {"valid_from": None, "valid_to": None},
                    "recorded_at": "2026-07-02T00:00:00Z",
                    "confidence": 1.0,
                    "sensitivity": "personal",
                    "provenance": {"source": "user", "session": None, "stated_by": None},
                }
            ],
            "observations": [],
            "traits": [],
            "interactions": [],
            "reminders": [],
            "user_preferences": [
                {
                    "key": "communication_philosophy",
                    "value": "Be direct.",
                    "updated_at": "2026-07-03T00:00:00Z",
                }
            ],
            "audit_log": [
                {
                    "id": "01J0000000000000000AUDIT01",
                    "ts": "2026-07-02T00:00:00Z",
                    "op": "create",
                    "entity_type": "fact",
                    "entity_id": "01J00000000000000000FACT01",
                    "payload": {"predicate": "city"},
                    "source": "user",
                }
            ],
        },
        "relationship_vocabulary": {
            "types": [
                {
                    "type": "colleague_of",
                    "inverse": None,
                    "symmetric": True,
                    "category": "professional",
                    "canonical": True,
                }
            ],
            "synonyms": [{"synonym": "colleague", "type": "colleague_of"}],
        },
        "changelog": [
            {
                "op_id": "01J000000000000000000OP001",
                "device_id": _DEVICE_ID,
                "hlc_physical_ms": 1755000000000,
                "hlc_logical": 3,
                "transaction_id": "01J0000000000000000000TX01",
                "entity_type": "fact",
                "entity_id": "01J00000000000000000FACT01",
                "op_kind": "create",
                "payload": {"predicate": "city", "value": "Berlin"},
                "changed_fields": [],
                "actor": {"source": "user"},
                "schema_version": 1,
                "inserted_at": "2026-07-02T00:00:00Z",
            }
        ],
    }


def _strict_models() -> list[type[BaseModel]]:
    return [
        value
        for value in vars(sync_bundle).values()
        if isinstance(value, type)
        and issubclass(value, StrictBundleModel)
        and value is not StrictBundleModel
    ]


def test_complete_version_one_document_parses() -> None:
    document = SyncBundleDocument.model_validate(_document())

    assert document.format == SYNC_BUNDLE_FORMAT
    assert document.version == SYNC_BUNDLE_VERSION
    assert document.origin_device_id == _DEVICE_ID
    assert document.watermark.hlc_physical_ms == 1755000000000
    assert [person.canonical_name for person in document.snapshot.people] == ["Alice"]
    assert document.snapshot.people[0].aliases[0].value == "Al"
    assert document.snapshot.user_preferences[0].value == "Be direct."
    assert document.relationship_vocabulary.synonyms[0].synonym == "colleague"
    assert document.changelog[0].payload == {"predicate": "city", "value": "Berlin"}


def test_wrong_format_is_rejected() -> None:
    payload = _document()
    payload["format"] = "people-context-export"

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize("version", [0, 2, "1"])
def test_unsupported_version_is_rejected(version: object) -> None:
    payload = _document()
    payload["version"] = version

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize(
    "remove",
    [
        ("format",),
        ("origin_device_id",),
        ("watermark",),
        ("changelog",),
        ("snapshot", "people"),
        ("relationship_vocabulary", "synonyms"),
    ],
)
def test_missing_required_field_is_rejected(remove: tuple[str, ...]) -> None:
    payload = _document()
    target: Any = payload
    for key in remove[:-1]:
        target = target[key]
    del target[remove[-1]]

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_missing_required_nested_row_field_is_rejected() -> None:
    payload = _document()
    del payload["snapshot"]["people"][0]["summary"]

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_unknown_top_level_field_is_rejected() -> None:
    payload = _document()
    payload["user_version"] = 4

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize(
    "path",
    [
        ("watermark",),
        ("devices", 0),
        ("snapshot",),
        ("snapshot", "people", 0),
        ("snapshot", "people", 0, "aliases", 0),
        ("snapshot", "facts", 0),
        ("snapshot", "facts", 0, "period"),
        ("snapshot", "facts", 0, "provenance"),
        ("snapshot", "user_preferences", 0),
        ("snapshot", "audit_log", 0),
        ("relationship_vocabulary",),
        ("relationship_vocabulary", "types", 0),
        ("relationship_vocabulary", "synonyms", 0),
        ("changelog", 0),
    ],
)
def test_unknown_nested_field_is_rejected(path: tuple[object, ...]) -> None:
    payload = _document()
    target: Any = payload
    for key in path:
        target = target[key]
    target["unexpected"] = "value"

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize("value", ["not-a-timestamp", "2026-07-19T12:00:00", 1755000000000])
def test_malformed_or_naive_timestamp_is_rejected(value: object) -> None:
    payload = _document()
    payload["created_at"] = value

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_naive_nested_timestamp_is_rejected() -> None:
    payload = _document()
    payload["snapshot"]["people"][0]["created_at"] = "2026-07-01T00:00:00"

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_offset_timestamps_are_normalized_to_utc() -> None:
    payload = _document()
    payload["created_at"] = "2026-07-19T14:00:00+02:00"

    document = SyncBundleDocument.model_validate(payload)

    assert document.created_at == datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    assert document.created_at.tzinfo is UTC


@pytest.mark.parametrize(
    "path",
    [
        ("origin_device_id",),
        ("devices", 0, "id"),
        ("snapshot", "people", 0, "id"),
        ("snapshot", "audit_log", 0, "entity_id"),
        ("changelog", 0, "op_id"),
        ("changelog", 0, "transaction_id"),
        ("relationship_vocabulary", "synonyms", 0, "synonym"),
    ],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_identifiers_are_rejected(path: tuple[object, ...], blank: str) -> None:
    payload = _document()
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = blank

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize("field", ["hlc_physical_ms", "hlc_logical"])
def test_negative_watermark_components_are_rejected(field: str) -> None:
    payload = _document()
    payload["watermark"][field] = -1

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_duplicate_document_parses_identically() -> None:
    payload = _document()

    first = SyncBundleDocument.model_validate(copy.deepcopy(payload))
    second = SyncBundleDocument.model_validate(copy.deepcopy(payload))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_no_bundle_model_field_has_a_silent_default() -> None:
    """Restore input must never be completed by defaults invented at parse time."""
    optional = [
        f"{model.__name__}.{name}"
        for model in _strict_models()
        for name, field in model.model_fields.items()
        if not field.is_required()
    ]

    assert optional == []


def test_every_bundle_model_forbids_unknown_fields() -> None:
    permissive = [model.__name__ for model in _strict_models() if model.model_config.get("extra") != "forbid"]

    assert permissive == []
    assert len(_strict_models()) > 15
