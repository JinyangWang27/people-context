"""Strict bootstrap sync-bundle contract tests."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from people_context.domain import sync_bundle
from people_context.domain.sync_bundle import (
    MAX_REPORTED_DETAILS,
    SQLITE_MAX_INTEGER,
    SYNC_BUNDLE_FORMAT,
    SYNC_BUNDLE_VERSION,
    InvalidBundleError,
    StrictBundleModel,
    SyncBundleDocument,
    validate_bundle_document,
)

_DEVICE_ID = "01J00000000000000000000DEV"
_OTHER_DEVICE_ID = "01J0000000000000000000DEV2"
_PERSON_ID = "01J000000000000000000PERSON"
_SOURCE_ID = "01J0000000000000000SOURCE1"
_BATCH_ID = "01J00000000000000000BATCH1"
_CANDIDATE_ID = "01J0000000000000000000CND1"


def _document() -> dict[str, Any]:
    """Return one complete, structurally valid current-version bundle document."""
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
        "imports": {
            "source_sessions": [
                {
                    "id": _SOURCE_ID,
                    "source_kind": "linkedin",
                    "label": "Connections export",
                    "external_source_id": None,
                    "content_digest": "a" * 64,
                    "extraction_fingerprint": "b" * 64,
                    "extraction_contract_revision": "linkedin.1",
                    "claim_key": f"linkedin\x1f{'a' * 64}\x1f{'b' * 64}",
                    "batch_id": _BATCH_ID,
                    "status": "partially_committed",
                    "created_at": "2026-07-03T00:00:00Z",
                }
            ],
            "candidate_mappings": [
                {
                    "candidate_id": _CANDIDATE_ID,
                    "batch_id": _BATCH_ID,
                    "source_session_id": _SOURCE_ID,
                    "disposition": "entity",
                    "entity_type": "person",
                    "entity_id": _PERSON_ID,
                    "created_at": "2026-07-03T00:00:00Z",
                }
            ],
            "staging": [
                {
                    "id": "01J0000000000000000STAGE01",
                    "batch_id": _BATCH_ID,
                    "source": "import/linkedin",
                    "candidate": {
                        "type": "person",
                        "name": "Alice",
                        "aliases": [],
                        "matched_person_id": _PERSON_ID,
                    },
                    "status": "pending",
                    "created_at": "2026-07-03T00:00:00Z",
                }
            ],
        },
    }


def _strict_models() -> list[type[BaseModel]]:
    return [
        value
        for value in vars(sync_bundle).values()
        if isinstance(value, type)
        and issubclass(value, StrictBundleModel)
        and value is not StrictBundleModel
    ]


def test_complete_current_version_document_parses() -> None:
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


@pytest.mark.parametrize("version", [0, 3, "2"])
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


def _validated(payload: dict[str, Any]) -> SyncBundleDocument:
    """Parse a payload structurally without applying the document-level rules."""
    return SyncBundleDocument.model_validate(payload)


def _interaction(interaction_id: str, participant_ids: list[str]) -> dict[str, Any]:
    return {
        "id": interaction_id,
        "summary": "Coffee",
        "occurred_at": "2026-07-04T00:00:00Z",
        "channel": None,
        "participant_ids": participant_ids,
        "sensitivity": "personal",
        "provenance": {"source": "user", "session": None, "stated_by": None},
    }


def test_complete_document_satisfies_every_document_level_rule() -> None:
    validate_bundle_document(_validated(_document()))


def test_relationship_may_reference_a_seeded_type_the_bundle_omits() -> None:
    """A destination always carries the seeded vocabulary, so omitting it is not dangling."""
    payload = _document()
    payload["snapshot"]["relationships"] = [
        {
            "id": "01J000000000000000000REL01",
            "subject_id": _PERSON_ID,
            "object_id": _PERSON_ID,
            "type": "friend_of",
            "label": None,
            "period": {"valid_from": None, "valid_to": None},
            "confidence": 1.0,
            "provenance": {"source": "user", "session": None, "stated_by": None},
            "created_at": "2026-07-04T00:00:00Z",
        }
    ]

    validate_bundle_document(_validated(payload))


def test_missing_origin_device_is_rejected() -> None:
    payload = _document()
    payload["origin_device_id"] = _OTHER_DEVICE_ID

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert error.value.code == "invalid_bundle"
    assert any("origin device is absent" in detail for detail in error.value.details)


def test_retired_origin_device_is_rejected() -> None:
    payload = _document()
    payload["devices"][0]["retired_at"] = "2026-07-10T00:00:00Z"

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any("origin device is retired" in detail for detail in error.value.details)


def test_dangling_changelog_device_is_rejected() -> None:
    payload = _document()
    payload["changelog"][0]["device_id"] = _OTHER_DEVICE_ID

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any("unbundled device" in detail for detail in error.value.details)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (("snapshot", "facts", 0, "person_id"), "fact"),
        (("snapshot", "audit_log", 0, "id"), None),
    ],
)
def test_dangling_domain_reference_is_rejected(path: tuple[object, ...], expected: str | None) -> None:
    payload = _document()
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "01J0000000000000000MISSING"
    if expected is None:
        # An audit id is not a foreign key; changing it must stay acceptable.
        validate_bundle_document(_validated(payload))
        return

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith(f"{expected} ") for detail in error.value.details)


def test_an_uncategorized_relationship_type_is_accepted() -> None:
    """A stored type with no vocabulary row is legal and reads as `uncategorized`.

    `SetRelationship` accepts any normalized type, so a database can hold one and `sync push`
    will export it. Requiring a vocabulary row here would make restore refuse a bundle that
    export legitimately produced.
    """
    payload = _document()
    payload["snapshot"]["relationships"] = [
        {
            "id": "01J000000000000000000REL01",
            "subject_id": _PERSON_ID,
            "object_id": _PERSON_ID,
            "type": "childhood_rival_of",
            "label": None,
            "period": {"valid_from": None, "valid_to": None},
            "confidence": 1.0,
            "provenance": {"source": "user", "session": None, "stated_by": None},
            "created_at": "2026-07-04T00:00:00Z",
        }
    ]

    validate_bundle_document(_validated(payload))


def test_dangling_vocabulary_references_are_rejected() -> None:
    payload = _document()
    payload["relationship_vocabulary"]["synonyms"][0]["type"] = "co_founder_of"
    payload["relationship_vocabulary"]["types"][0]["symmetric"] = False
    payload["relationship_vocabulary"]["types"][0]["inverse"] = "co_founded_by"

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any("unknown type: co_founder_of" in detail for detail in error.value.details)
    assert any("unknown inverse: co_founded_by" in detail for detail in error.value.details)


def test_dangling_interaction_participant_is_rejected() -> None:
    payload = _document()
    payload["snapshot"]["interactions"] = [
        _interaction("01J000000000000000000INT01", ["01J0000000000000000MISSING"])
    ]

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any("unknown participant" in detail for detail in error.value.details)


@pytest.mark.parametrize("target", ["changelog", "device"])
def test_watermark_below_bundled_history_is_rejected(target: str) -> None:
    payload = _document()
    if target == "changelog":
        payload["changelog"][0]["hlc_logical"] = 4
    else:
        payload["devices"][0]["hlc_physical_ms"] = 1755000000001

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any("ahead of the bundle watermark" in detail for detail in error.value.details)


def test_watermark_equal_to_the_highest_entry_is_accepted() -> None:
    payload = _document()
    payload["watermark"] = {"hlc_physical_ms": 1755000000000, "hlc_logical": 3}

    validate_bundle_document(_validated(payload))


@pytest.mark.parametrize(
    ("collection", "label"),
    [
        ("devices", "device id"),
        ("changelog", "changelog op_id"),
    ],
)
def test_duplicate_top_level_ids_are_rejected(collection: str, label: str) -> None:
    payload = _document()
    payload[collection].append(copy.deepcopy(payload[collection][0]))

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith(f"duplicate {label}") for detail in error.value.details)


@pytest.mark.parametrize(
    ("collection", "label"),
    [
        ("people", "person id"),
        ("facts", "fact id"),
        ("user_preferences", "preference key"),
        ("audit_log", "audit entry id"),
    ],
)
def test_duplicate_snapshot_primary_keys_are_rejected(collection: str, label: str) -> None:
    payload = _document()
    payload["snapshot"][collection].append(copy.deepcopy(payload["snapshot"][collection][0]))

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith(f"duplicate {label}") for detail in error.value.details)


def test_duplicate_alias_id_across_people_is_rejected() -> None:
    payload = _document()
    second = copy.deepcopy(payload["snapshot"]["people"][0])
    second["id"] = "01J00000000000000PERSON002"
    payload["snapshot"]["people"].append(second)

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith("duplicate alias id") for detail in error.value.details)


def test_duplicate_interaction_participant_is_rejected() -> None:
    payload = _document()
    payload["snapshot"]["interactions"] = [
        _interaction("01J000000000000000000INT01", [_PERSON_ID, _PERSON_ID])
    ]

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith("duplicate participant of interaction") for detail in error.value.details)


def test_duplicate_vocabulary_keys_are_rejected() -> None:
    payload = _document()
    payload["relationship_vocabulary"]["types"].append(
        copy.deepcopy(payload["relationship_vocabulary"]["types"][0])
    )
    payload["relationship_vocabulary"]["synonyms"].append(
        copy.deepcopy(payload["relationship_vocabulary"]["synonyms"][0])
    )

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert any(detail.startswith("duplicate relationship type") for detail in error.value.details)
    assert any(detail.startswith("duplicate relationship synonym") for detail in error.value.details)


def test_reported_reasons_are_bounded() -> None:
    """A badly corrupted document must not turn one refusal into an unbounded message."""
    payload = _document()
    payload["snapshot"]["facts"] = [
        {
            **copy.deepcopy(payload["snapshot"]["facts"][0]),
            "id": f"01J0000000000000000FACT{index:03d}",
            "person_id": f"01J000000000000000MISS{index:04d}",
        }
        for index in range(MAX_REPORTED_DETAILS + 5)
    ]

    with pytest.raises(InvalidBundleError) as error:
        validate_bundle_document(_validated(payload))

    assert len(error.value.details) == MAX_REPORTED_DETAILS + 1
    assert error.value.details[-1] == "and 5 further reason(s)"


@pytest.mark.parametrize(
    "path",
    [
        ("watermark", "hlc_physical_ms"),
        ("watermark", "hlc_logical"),
        ("devices", 0, "hlc_physical_ms"),
        ("devices", 0, "hlc_logical"),
        ("changelog", 0, "hlc_physical_ms"),
        ("changelog", 0, "hlc_logical"),
    ],
)
def test_hlc_components_beyond_sqlite_storage_are_rejected(path: tuple[object, ...]) -> None:
    """An unstorable integer must fail at parse time, not as an OverflowError mid-restore."""
    payload = _document()
    target: Any = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = SQLITE_MAX_INTEGER

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_oversized_changelog_schema_version_is_rejected() -> None:
    payload = _document()
    payload["changelog"][0]["schema_version"] = SQLITE_MAX_INTEGER + 1

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_the_largest_storable_hlc_components_still_parse() -> None:
    """The bound leaves exactly one increment of headroom for ``observe()``, no more."""
    payload = _document()
    largest = SQLITE_MAX_INTEGER - 1
    payload["watermark"] = {"hlc_physical_ms": largest, "hlc_logical": largest}
    payload["devices"][0]["hlc_physical_ms"] = largest
    payload["devices"][0]["hlc_logical"] = largest
    payload["changelog"][0]["hlc_physical_ms"] = largest
    payload["changelog"][0]["hlc_logical"] = largest

    validate_bundle_document(_validated(payload))


@pytest.mark.parametrize(
    "collection",
    ["affiliations", "relationships", "facts"],
)
def test_an_inverted_validity_period_is_rejected(collection: str) -> None:
    """Ordinary reads rehydrate `ValidityPeriod`, so an inverted window must never commit."""
    payload = _document()
    payload["snapshot"][collection] = [_row_with_period(collection)]

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def _row_with_period(collection: str) -> dict[str, Any]:
    period = {"valid_from": "2026-07-10", "valid_to": "2026-07-01"}
    provenance = {"source": "user", "session": None, "stated_by": None}
    if collection == "affiliations":
        return {
            "id": "01J000000000000000000AFF01",
            "person_id": _PERSON_ID,
            "org_id": "01J000000000000000000ORG01",
            "role": "Engineer",
            "period": period,
            "confidence": 1.0,
            "provenance": provenance,
            "created_at": "2026-07-04T00:00:00Z",
        }
    if collection == "relationships":
        return {
            "id": "01J000000000000000000REL01",
            "subject_id": _PERSON_ID,
            "object_id": _PERSON_ID,
            "type": "friend_of",
            "label": None,
            "period": period,
            "confidence": 1.0,
            "provenance": provenance,
            "created_at": "2026-07-04T00:00:00Z",
        }
    return {
        "id": "01J00000000000000000FACT99",
        "person_id": _PERSON_ID,
        "predicate": "role",
        "value": "Engineer",
        "period": period,
        "recorded_at": "2026-07-02T00:00:00Z",
        "confidence": 1.0,
        "sensitivity": "personal",
        "provenance": provenance,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"symmetric": True, "inverse": "colleague_of"},
        {"symmetric": False, "canonical": False, "inverse": None},
    ],
)
def test_an_impossible_relationship_direction_is_rejected(overrides: dict[str, Any]) -> None:
    """The vocabulary store rehydrates `RelationshipType`; an impossible row must not commit."""
    payload = _document()
    payload["relationship_vocabulary"]["types"][0].update(overrides)

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)
