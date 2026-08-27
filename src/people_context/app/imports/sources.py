"""Bounded source-receipt metadata and the duplicate-claim refusal contract.

A source receipt is metadata *about* personal material. It is not the material, and it is not an
anonymization of it: a digest and a fingerprint are idempotency keys, and the file they identify
is still someone's mailbox or chat export. Everything here is therefore bounded at the process
boundary and refused without echoing what was rejected.

The one field with a semantic rule is ``source_kind``. It is a machine category — ``linkedin``,
``ics``, ``meeting_transcript`` — and never a human description such as ``interview_with_alice``,
because a terminal redacted receipt keeps its canonical claim key forever while hard forget
scrubs every caller-authored field around it. A kind that carried a name would survive erasure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from people_context.app.imports.models import ImportPipelineError
from people_context.domain.import_provenance import (
    check_contract_revision,
    check_hex64,
    check_opaque_label,
    check_source_kind,
)
from people_context.ports.sources import SourceSessionClaim

#: Stable refusal for receipt metadata outside its declared bounds or alphabet.
INVALID_SOURCE_METADATA: Final = "invalid_source_metadata"

#: Stable refusal for a default duplicate claim whose prior batch was fully hard-forgotten.
SOURCE_PREVIOUSLY_REDACTED: Final = "source_previously_redacted"


def _reject(field: str, reason: str) -> ImportPipelineError:
    """Refuse one metadata field by name and rule, never by value.

    Receipt metadata is caller-authored text about personal material, so a refusal that quoted it
    would put the rejected label straight back into a terminal, a log, or a JSON document.
    """
    return ImportPipelineError(
        INVALID_SOURCE_METADATA,
        f"source metadata field '{field}' {reason}",
        field=field,
    )


def _checked(field: str, value: str | None, rule: Callable[[str], str]) -> str | None:
    """Apply one domain rule to an optional field, refusing by field name rather than by value.

    A field left blank is absent rather than invalid: it carries no claim and no wording, so
    there is nothing to refuse.
    """
    if value is None or not value.strip():
        return None
    try:
        return rule(value)
    except ValueError as exc:
        raise _reject(field, str(exc)) from None


def validate_source_kind(value: str) -> str:
    """Return the accepted machine category, or refuse it."""
    try:
        return check_source_kind(value)
    except ValueError as exc:
        raise _reject("source_kind", str(exc)) from None


def validate_opaque_label(field: str, value: str | None) -> str | None:
    """Return one bounded opaque caller string, or refuse it."""
    return _checked(field, value, check_opaque_label)


def validate_digest(field: str, value: str | None) -> str | None:
    """Return one accepted SHA-256 digest or extraction fingerprint, or refuse it."""
    return _checked(field, value, check_hex64)


def validate_contract_revision(value: str | None) -> str | None:
    """Return one accepted extraction-contract revision identifier, or refuse it."""
    return _checked("extraction_contract_revision", value, check_contract_revision)


def require_source_kind_for(**metadata: str | None) -> None:
    """Refuse receipt metadata offered without the kind that would record it.

    A caller who passes a digest is asking for duplicate protection. Silently dropping it because
    no `source_kind` accompanied it would report an untracked staging run as an ordinary success
    while quietly withholding the very guarantee the argument requested.
    """
    supplied = sorted(name for name, value in metadata.items() if value is not None and value.strip())
    if supplied:
        raise _reject(
            "source_kind",
            f"is required when supplying receipt metadata ({', '.join(supplied)})",
        )


def build_source_claim(
    *,
    source_kind: str,
    content_digest: str | None = None,
    extraction_fingerprint: str | None = None,
    extraction_contract_revision: str | None = None,
    label: str | None = None,
    external_source_id: str | None = None,
    forced: bool = False,
) -> SourceSessionClaim:
    """Validate every receipt field at the boundary and return the claim it asserts.

    A fingerprint without a digest is refused rather than quietly dropped: it would describe the
    configuration of an extraction over bytes People Context cannot identify, which is a claim
    about nothing.
    """
    kind = validate_source_kind(source_kind)
    digest = validate_digest("content_digest", content_digest)
    fingerprint = validate_digest("extraction_fingerprint", extraction_fingerprint)
    if fingerprint is not None and digest is None:
        raise _reject("extraction_fingerprint", "requires a content_digest")
    return SourceSessionClaim(
        source_kind=kind,
        content_digest=digest,
        extraction_fingerprint=fingerprint,
        extraction_contract_revision=validate_contract_revision(extraction_contract_revision),
        label=validate_opaque_label("label", label),
        external_source_id=validate_opaque_label("external_source_id", external_source_id),
        forced=forced,
    )


def source_previously_redacted_error(source_session_id: str) -> ImportPipelineError:
    """Refuse a default duplicate claim that resolves to a terminal redacted receipt.

    The prior batch was hard-forgotten, so there is deliberately no batch association left to
    reuse. Fabricating one would hand the caller a batch id that reviews and commits nothing;
    reusing the old one would resurrect an association erasure removed. The refusal names the
    stable code and the fact that only an explicit reprocessing gets past it, and nothing else —
    not the former label, not the former batch, not when any of it happened.

    How a caller *performs* that reprocessing differs per entry point, so the wording deliberately
    stops short of naming one: this same refusal is raised for a CLI file import, for agent-staged
    candidates, and over MCP, and each of those opts out of the duplicate rule differently. The
    boundary that raised it appends its own route.
    """
    return ImportPipelineError(
        SOURCE_PREVIOUSLY_REDACTED,
        "this source was previously imported and then fully forgotten; "
        "staging it again must be an explicit reprocessing",
        source_session_id=source_session_id,
    )
