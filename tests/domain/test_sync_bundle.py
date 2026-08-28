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


def _imports(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["imports"]


def test_a_claim_key_without_a_digest_is_rejected() -> None:
    payload = _document()
    _imports(payload)["source_sessions"][0]["content_digest"] = None

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_redacted_session_without_a_digest_is_rejected() -> None:
    """A terminal receipt exists to preserve a claim; without one there is nothing to preserve.

    The claim-key requirement is what refuses this: a digestless receipt can hold no key, and a
    key is what duplicate detection would have to find. Both nulls are checked together here.
    """
    payload = _document()
    session = _imports(payload)["source_sessions"][0]
    session.update(
        {
            "status": "redacted",
            "content_digest": None,
            "claim_key": None,
            "label": None,
            "external_source_id": None,
            "extraction_contract_revision": None,
            "batch_id": None,
        }
    )

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("label", "Interview with Alice"),
        ("external_source_id", "NOTES-1"),
        ("extraction_contract_revision", "linkedin.1"),
        ("batch_id", _BATCH_ID),
    ],
)
def test_a_redacted_session_carrying_cleared_state_is_rejected(field: str, value: str) -> None:
    payload = _document()
    session = _imports(payload)["source_sessions"][0]
    session.update(
        {
            "status": "redacted",
            "claim_key": None,
            "label": None,
            "external_source_id": None,
            "extraction_contract_revision": None,
            "batch_id": None,
        }
    )
    session[field] = value

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_an_entity_mapping_without_an_entity_id_is_rejected() -> None:
    payload = _document()
    _imports(payload)["candidate_mappings"][0]["entity_id"] = None

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_merged_away_mapping_naming_an_entity_is_rejected() -> None:
    payload = _document()
    _imports(payload)["candidate_mappings"][0].update(
        {"disposition": "merged_away", "entity_type": "relationship"}
    )

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_merged_away_mapping_of_another_entity_type_is_rejected() -> None:
    """The terminal outcome exists only for a relationship edge a merge removed."""
    payload = _document()
    _imports(payload)["candidate_mappings"][0].update(
        {"disposition": "merged_away", "entity_type": "person", "entity_id": None}
    )

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_merged_away_relationship_mapping_needs_no_entity() -> None:
    payload = _document()
    _imports(payload)["candidate_mappings"][0].update(
        {"disposition": "merged_away", "entity_type": "relationship", "entity_id": None}
    )

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


def test_a_mapping_referencing_an_unbundled_source_session_is_rejected() -> None:
    payload = _document()
    _imports(payload)["candidate_mappings"][0]["source_session_id"] = "01J000000000000000MISSING1"

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("unbundled source session" in detail for detail in excinfo.value.details)


def test_a_mapping_of_an_unsupported_entity_type_is_rejected() -> None:
    payload = _document()
    _imports(payload)["candidate_mappings"][0]["entity_type"] = "reminder"

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("unsupported entity type" in detail for detail in excinfo.value.details)


def test_duplicate_import_identifiers_are_rejected() -> None:
    payload = _document()
    imports = _imports(payload)
    imports["candidate_mappings"].append(dict(imports["candidate_mappings"][0]))

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("duplicate candidate mapping id" in detail for detail in excinfo.value.details)


def test_two_sessions_claiming_one_source_are_rejected() -> None:
    payload = _document()
    imports = _imports(payload)
    twin = dict(imports["source_sessions"][0])
    twin["id"] = "01J0000000000000000SOURCE2"
    twin["batch_id"] = "01J00000000000000000BATCH2"
    imports["source_sessions"].append(twin)

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("duplicate source claim" in detail for detail in excinfo.value.details)


def test_a_mapping_filed_under_another_batch_is_rejected() -> None:
    """Commit reads mappings by the receipt's batch, so a mismatched one would be invisible."""
    payload = _document()
    _imports(payload)["candidate_mappings"][0]["batch_id"] = "01J00000000000000000BATCH9"

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("does not own" in detail for detail in excinfo.value.details)


@pytest.mark.parametrize(
    "claim_key",
    [
        f"linkedin\x1f{'a' * 64}\x1f{'c' * 64}",
        f"whatsapp\x1f{'a' * 64}\x1f{'b' * 64}",
        f"linkedin\x1f{'c' * 64}\x1f{'b' * 64}",
        f"linkedin\x1f{'a' * 64}\x1ffingerprint-absent",
    ],
)
def test_a_claim_key_that_is_not_the_composition_of_its_own_fields_is_rejected(claim_key: str) -> None:
    """Duplicate detection looks a receipt up by the key composed from these very fields.

    A key that disagrees with them would either miss its own receipt and stage the source a second
    time, or sit on the key of an unrelated source and suppress that source's legitimate import.
    """
    payload = _document()
    _imports(payload)["source_sessions"][0]["claim_key"] = claim_key

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_an_absent_fingerprint_composes_its_claim_through_the_explicit_sentinel() -> None:
    """SQLite treats NULLs in a UNIQUE index as distinct, so absence is one named state."""
    payload = _document()
    session = _imports(payload)["source_sessions"][0]
    session["extraction_fingerprint"] = None
    session["extraction_contract_revision"] = None
    session["claim_key"] = f"linkedin\x1f{'a' * 64}\x1ffingerprint-absent"

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


def test_two_sessions_sharing_one_batch_are_rejected() -> None:
    """One batch belongs to one receipt: the store's batch lookup returns a single session.

    Two receipts on one batch would leave that lookup returning either row, so a later commit
    could attribute its mappings and its status change to the receipt that does not own the batch.
    """
    payload = _document()
    imports = _imports(payload)
    twin = dict(imports["source_sessions"][0])
    twin.update(
        {
            "id": "01J0000000000000000SOURCE2",
            "content_digest": "c" * 64,
            "claim_key": f"linkedin\x1f{'c' * 64}\x1f{'b' * 64}",
        }
    )
    imports["source_sessions"].append(twin)

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("duplicate source session batch" in detail for detail in excinfo.value.details)


def test_a_mapping_naming_a_person_who_is_not_active_is_rejected() -> None:
    """A merge retargets person mappings and a forget removes them, so none points at a retired id.

    Restoring one that did would let a later commit resolve a dependant through that identity and
    then fail the child write's own active-person check, taking the whole commit down with it.
    """
    payload = _document()
    retired = copy.deepcopy(payload["snapshot"]["people"][0])
    retired.update({"id": "01J00000000000000000GONE01", "aliases": [], "deleted_at": "2026-07-04T00:00:00Z"})
    payload["snapshot"]["people"].append(retired)
    _imports(payload)["candidate_mappings"][0]["entity_id"] = retired["id"]

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("not active in the bundle" in detail for detail in excinfo.value.details)


def test_a_redacted_session_without_its_claim_key_is_rejected() -> None:
    """Duplicate detection finds a terminal receipt by its key, not by its digest.

    A redacted row carrying only the digest is invisible to that lookup, so the forgotten source
    would stage fresh instead of being refused — the terminal-forget contract undone by a null.
    """
    payload = _document()
    session = _imports(payload)["source_sessions"][0]
    session.update(
        {
            "status": "redacted",
            "claim_key": None,
            "label": None,
            "external_source_id": None,
            "extraction_contract_revision": None,
            "batch_id": None,
        }
    )

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_staged_session_owning_no_reviewable_row_is_rejected() -> None:
    """A staged receipt reports its batch as an existing import, so review must be able to find it.

    Nothing has committed, so mappings cannot stand in for the rows. Hard forget never leaves this
    state either: emptying a staged receipt reduces it to a terminal claim or deletes it outright.
    """
    payload = _document()
    imports = _imports(payload)
    imports["source_sessions"][0]["status"] = "staged"
    imports["candidate_mappings"] = []
    imports["staging"] = []

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("owns no reviewable staging row" in detail for detail in excinfo.value.details)


def test_a_live_session_with_nothing_behind_it_is_rejected() -> None:
    payload = _document()
    imports = _imports(payload)
    imports["candidate_mappings"] = []
    imports["staging"] = []

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("owns no mapping and no reviewable staging row" in detail for detail in excinfo.value.details)


def test_a_committed_staging_row_alone_does_not_keep_a_session_live() -> None:
    """A committed row is not reviewable, so it cannot be what a duplicate report points at."""
    payload = _document()
    imports = _imports(payload)
    imports["candidate_mappings"] = []
    imports["staging"][0]["status"] = "committed"

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("owns no mapping and no reviewable staging row" in detail for detail in excinfo.value.details)


def test_a_partially_committed_session_may_own_only_its_surviving_mappings() -> None:
    """Exactly what hard forget produces when it erases a batch's reviewable rows but not its records.

    `tests/adapters/sqlite/test_source_forget.py::
    test_a_partial_commit_whose_pending_rows_are_all_forgotten_keeps_its_mappings` pins that this
    state comes out of a real database, so the validator must keep accepting it.
    """
    payload = _document()
    imports = _imports(payload)
    imports["staging"] = []

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


@pytest.mark.parametrize(
    "candidate",
    [
        {"type": "fact", "person_candidate_id": "01J0000000000000000STAGE01", "value": "Berlin"},
        {"type": "fact", "person_candidate_id": "01J0000000000000000STAGE01", "predicate": "city"},
        {"type": "observation", "person_candidate_id": "01J0000000000000000STAGE01"},
        {"type": "person", "aliases": []},
        {"type": "person", "name": "Alice"},
        {
            "type": "trait",
            "person_candidate_id": "01J0000000000000000STAGE01",
            "category": "communication_style",
            "value": "Direct",
            "confidence": 0.6,
        },
    ],
)
def test_a_restored_candidate_missing_a_field_commit_indexes_is_rejected(candidate: dict[str, Any]) -> None:
    """These do not go unresolved at commit — they raise `KeyError` mid-transaction.

    By then the restore has been accepted and earlier candidates in the same commit have written,
    so the batch has to be refused here or not at all.
    """
    payload = _document()
    _imports(payload)["staging"][0]["candidate"] = candidate

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("aliases", "alice@example.com"),
        ("match_count", "two"),
        ("match_count", True),
    ],
)
def test_a_restored_candidate_holding_a_declared_field_as_the_wrong_primitive_is_rejected(
    field: str,
    value: object,
) -> None:
    """A present-but-wrongly-typed field reaches the durable write and fails it mid-commit.

    A boolean is included on purpose: `bool` is an `int` in Python, and a boolean count is not a
    count.
    """
    payload = _document()
    _imports(payload)["staging"][0]["candidate"][field] = value

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_restored_candidate_carrying_an_undeclared_field_is_rejected() -> None:
    """Staging is where extraction output stops being prose; an invented key is that prose.

    Review would display it and every later bundle would carry it, so the accepted set is closed
    exactly as the models that produce these rows forbid extras.
    """
    payload = _document()
    _imports(payload)["staging"][0]["candidate"]["raw_body"] = "the whole transcript"

    with pytest.raises(ValidationError) as excinfo:
        SyncBundleDocument.model_validate(payload)

    # The key is named because it is the caller's own invention; the value never is.
    assert "raw_body" in str(excinfo.value)
    assert "the whole transcript" not in str(excinfo.value)


def test_a_restored_candidate_may_omit_every_optional_field() -> None:
    payload = _document()
    _imports(payload)["staging"][0]["candidate"] = {"type": "person", "name": "Alice", "aliases": []}

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


def test_a_committed_staging_row_without_its_outcome_is_rejected() -> None:
    """The mapping and the transition to committed are written in one unit of work.

    Without the mapping, commit resolves a dependant by matching the stored name — the heuristic
    the mapping exists to replace, and one that lands on a different identity once names are
    ambiguous.
    """
    payload = _document()
    imports = _imports(payload)
    imports["staging"][0]["status"] = "committed"

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("carries no outcome mapping" in detail for detail in excinfo.value.details)


def test_a_pending_staging_row_that_already_has_an_outcome_is_rejected() -> None:
    """A mapping and its row's transition to committed are written in one unit of work.

    Restoring a pending row that already carries an outcome would let commit produce a second
    entity and overwrite the mapping, orphaning the first record from the source that produced it.
    """
    payload = _document()
    imports = _imports(payload)
    imports["candidate_mappings"][0]["candidate_id"] = imports["staging"][0]["id"]

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("already has a committed outcome" in detail for detail in excinfo.value.details)


def test_a_committed_staging_row_may_carry_its_own_outcome() -> None:
    payload = _document()
    imports = _imports(payload)
    imports["candidate_mappings"][0]["candidate_id"] = imports["staging"][0]["id"]
    imports["staging"][0]["status"] = "committed"

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


@pytest.mark.parametrize("kind", ["Interview with Alice", "", "a" * 129, "réunion"])
def test_a_restored_source_kind_is_held_to_the_machine_alphabet(kind: str) -> None:
    """Forget keeps the kind on a terminal receipt, so it must never hold a name."""
    payload = _document()
    _imports(payload)["source_sessions"][0]["source_kind"] = kind

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("label", "x" * 257),
        ("external_source_id", "x" * 257),
        ("content_digest", "NOT-A-DIGEST"),
        ("extraction_fingerprint", "A" * 64),
        ("extraction_contract_revision", "rev 1"),
    ],
)
def test_restored_receipt_metadata_is_held_to_its_declared_bounds(field: str, value: str) -> None:
    payload = _document()
    _imports(payload)["source_sessions"][0][field] = value

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


@pytest.mark.parametrize("status", ["reviewed", "", "PENDING"])
def test_a_staging_row_with_an_unknown_status_is_rejected(status: str) -> None:
    payload = _document()
    _imports(payload)["staging"][0]["status"] = status

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_staged_candidate_of_an_unknown_type_is_rejected() -> None:
    payload = _document()
    _imports(payload)["staging"][0]["candidate"] = {"type": "invoice", "body": "raw source text"}

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_staged_candidate_missing_a_reference_commit_indexes_is_rejected() -> None:
    """`{"type": "fact"}` would restore fine and then raise KeyError mid-commit."""
    payload = _document()
    _imports(payload)["staging"][0]["candidate"] = {"type": "fact", "predicate": "city", "value": "Berlin"}

    with pytest.raises(ValidationError):
        SyncBundleDocument.model_validate(payload)


def test_a_staged_candidate_referencing_a_row_outside_its_batch_is_rejected() -> None:
    payload = _document()
    imports = _imports(payload)
    imports["staging"].append(
        {
            "id": "01J0000000000000000STAGE02",
            "batch_id": _BATCH_ID,
            "source": "import/linkedin",
            "candidate": {
                "type": "fact",
                "person_candidate_id": "01J000000000000000MISSING1",
                "predicate": "city",
                "value": "Berlin",
            },
            "status": "pending",
            "created_at": "2026-07-03T00:00:00Z",
        }
    )

    with pytest.raises(InvalidBundleError) as excinfo:
        validate_bundle_document(SyncBundleDocument.model_validate(payload))

    assert any("outside its batch" in detail for detail in excinfo.value.details)


def test_a_staged_candidate_referencing_a_row_in_its_batch_is_accepted() -> None:
    payload = _document()
    imports = _imports(payload)
    imports["staging"].append(
        {
            "id": "01J0000000000000000STAGE02",
            "batch_id": _BATCH_ID,
            "source": "import/linkedin",
            "candidate": {
                "type": "fact",
                "person_candidate_id": "01J0000000000000000STAGE01",
                "predicate": "city",
                "value": "Berlin",
            },
            "status": "pending",
            "created_at": "2026-07-03T00:00:00Z",
        }
    )

    validate_bundle_document(SyncBundleDocument.model_validate(payload))


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
