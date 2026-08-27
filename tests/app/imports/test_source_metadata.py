"""Bounds, alphabet, and claim-identity rules for import source receipts."""

from __future__ import annotations

import pytest

from people_context.app.imports import ImportPipelineError
from people_context.app.imports.sources import (
    INVALID_SOURCE_METADATA,
    MAX_CONTRACT_REVISION_CHARS,
    MAX_SOURCE_KIND_CHARS,
    MAX_SOURCE_LABEL_CHARS,
    build_source_claim,
)
from people_context.ports.sources import (
    EXTRACTION_FINGERPRINT_ABSENT,
    compose_claim_key,
)

_DIGEST = "a" * 64
_FINGERPRINT = "b" * 64

#: A value that must never travel back out through a refusal message.
_SENTINEL = "INTERVIEW-WITH-ALICE-MUST-NOT-LEAK-7f21"


def test_a_complete_claim_is_accepted_verbatim() -> None:
    claim = build_source_claim(
        source_kind="meeting_transcript",
        content_digest=_DIGEST,
        extraction_fingerprint=_FINGERPRINT,
        extraction_contract_revision="whatsapp.1",
        label="Weekly sync",
        external_source_id="Notes-2026-07-20",
    )

    assert claim.source_kind == "meeting_transcript"
    assert claim.content_digest == _DIGEST
    assert claim.extraction_fingerprint == _FINGERPRINT
    assert claim.label == "Weekly sync"
    # An external id belongs to another system, so its case and punctuation are preserved.
    assert claim.external_source_id == "Notes-2026-07-20"


@pytest.mark.parametrize(
    "kind",
    [
        "",
        "   ",
        "interview with alice",
        "réunion",
        "kind;drop",
        "a" * (MAX_SOURCE_KIND_CHARS + 1),
    ],
)
def test_a_source_kind_outside_the_machine_alphabet_is_refused(kind: str) -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind=kind)

    assert excinfo.value.code == INVALID_SOURCE_METADATA
    assert excinfo.value.details["field"] == "source_kind"


@pytest.mark.parametrize("kind", ["linkedin", "ics", "meeting_transcript", "vendor.tool/export", "a-b_c.1"])
def test_a_machine_category_is_accepted(kind: str) -> None:
    assert build_source_claim(source_kind=kind).source_kind == kind


def test_an_oversized_label_is_refused_without_echoing_it() -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind="linkedin", label=_SENTINEL + "x" * MAX_SOURCE_LABEL_CHARS)

    assert excinfo.value.code == INVALID_SOURCE_METADATA
    assert _SENTINEL not in str(excinfo.value)
    assert _SENTINEL not in repr(excinfo.value.details)


def test_an_oversized_external_source_id_is_refused_without_echoing_it() -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind="linkedin", external_source_id=_SENTINEL * 20)

    assert excinfo.value.details["field"] == "external_source_id"
    assert _SENTINEL not in str(excinfo.value)


def test_a_blank_label_is_treated_as_absent_rather_than_stored() -> None:
    claim = build_source_claim(source_kind="linkedin", label="   ", external_source_id="")

    assert claim.label is None
    assert claim.external_source_id is None


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "not-a-digest"])
def test_a_digest_outside_the_accepted_spelling_is_refused(digest: str) -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind="linkedin", content_digest=digest)

    assert excinfo.value.details["field"] == "content_digest"


def test_a_fingerprint_without_a_digest_is_refused() -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind="linkedin", extraction_fingerprint=_FINGERPRINT)

    assert excinfo.value.details["field"] == "extraction_fingerprint"


@pytest.mark.parametrize("revision", ["a" * (MAX_CONTRACT_REVISION_CHARS + 1), "rev/1", "rev 1", "rév"])
def test_a_contract_revision_outside_its_alphabet_is_refused(revision: str) -> None:
    with pytest.raises(ImportPipelineError) as excinfo:
        build_source_claim(source_kind="linkedin", content_digest=_DIGEST, extraction_contract_revision=revision)

    assert excinfo.value.details["field"] == "extraction_contract_revision"


# -- claim identity ----------------------------------------------------


def test_a_digestless_claim_asserts_no_canonical_key() -> None:
    claim = build_source_claim(source_kind="meeting_transcript", label="Weekly sync")

    assert claim.claim_key is None


def test_an_absent_fingerprint_becomes_an_explicit_state_rather_than_a_null() -> None:
    claim = build_source_claim(source_kind="linkedin", content_digest=_DIGEST)

    assert claim.claim_key is not None
    assert EXTRACTION_FINGERPRINT_ABSENT in claim.claim_key


def test_a_supplied_fingerprint_stays_distinct_from_the_absent_state() -> None:
    absent = build_source_claim(source_kind="linkedin", content_digest=_DIGEST)
    supplied = build_source_claim(
        source_kind="linkedin",
        content_digest=_DIGEST,
        extraction_fingerprint=_FINGERPRINT,
    )

    assert absent.claim_key != supplied.claim_key


def test_the_absence_sentinel_cannot_collide_with_a_real_fingerprint() -> None:
    assert len(EXTRACTION_FINGERPRINT_ABSENT) != 64


def test_the_source_kind_scopes_the_claim() -> None:
    first = compose_claim_key("linkedin", _DIGEST, _FINGERPRINT)
    second = compose_claim_key("outlook", _DIGEST, _FINGERPRINT)

    assert first != second


def test_a_forced_claim_competes_for_no_canonical_key() -> None:
    forced = build_source_claim(
        source_kind="linkedin",
        content_digest=_DIGEST,
        extraction_fingerprint=_FINGERPRINT,
        forced=True,
    )

    assert forced.claim_key is None
    # It keeps the identifying metadata; only the canonical claim is given up.
    assert forced.content_digest == _DIGEST
    assert forced.extraction_fingerprint == _FINGERPRINT
