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

import re
from typing import Final

from people_context.app.imports.models import ImportPipelineError
from people_context.ports.sources import SourceSessionClaim

#: Characters a bounded machine source category may carry.
MAX_SOURCE_KIND_CHARS: Final = 128

#: Characters an optional caller-authored label or external source id may carry.
MAX_SOURCE_LABEL_CHARS: Final = 256

#: Characters an optional extraction-contract revision identifier may carry.
MAX_CONTRACT_REVISION_CHARS: Final = 64

#: A conservative machine-identifier alphabet: a source class or adapter name, not a title.
_SOURCE_KIND_RE: Final = re.compile(r"\A[A-Za-z0-9._/-]+\Z")

#: The same idea one character narrower — a revision identifier names no hierarchy.
_CONTRACT_REVISION_RE: Final = re.compile(r"\A[A-Za-z0-9._-]+\Z")

#: A SHA-256 digest or extraction fingerprint, in exactly one accepted spelling.
_HEX64_RE: Final = re.compile(r"\A[0-9a-f]{64}\Z")

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


def validate_source_kind(value: str) -> str:
    """Return the accepted machine category, or refuse it."""
    kind = value.strip()
    if not kind:
        raise _reject("source_kind", "must not be blank")
    if len(kind) > MAX_SOURCE_KIND_CHARS:
        raise _reject("source_kind", f"is at most {MAX_SOURCE_KIND_CHARS} characters")
    if not _SOURCE_KIND_RE.match(kind):
        raise _reject(
            "source_kind",
            "must be a machine category of ASCII letters, digits, '.', '_', '-', or '/'",
        )
    return kind


def validate_opaque_label(field: str, value: str | None) -> str | None:
    """Return one bounded opaque caller string, or refuse it.

    Opaque means opaque: an external source id is not case-folded or otherwise rewritten, because
    normalizing an identifier whose semantics belong to another system would silently change what
    it refers to. Only surrounding whitespace, which no identifier depends on, is removed.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_SOURCE_LABEL_CHARS:
        raise _reject(field, f"is at most {MAX_SOURCE_LABEL_CHARS} characters")
    return text


def validate_digest(field: str, value: str | None) -> str | None:
    """Return one accepted SHA-256 digest or extraction fingerprint, or refuse it."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not _HEX64_RE.match(text):
        raise _reject(field, "must be exactly 64 lowercase hexadecimal characters")
    return text


def validate_contract_revision(value: str | None) -> str | None:
    """Return one accepted extraction-contract revision identifier, or refuse it."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_CONTRACT_REVISION_CHARS:
        raise _reject("extraction_contract_revision", f"is at most {MAX_CONTRACT_REVISION_CHARS} characters")
    if not _CONTRACT_REVISION_RE.match(text):
        raise _reject(
            "extraction_contract_revision",
            "must be ASCII letters, digits, '.', '_', or '-'",
        )
    return text


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
    stable code and the intentional route through explicit reprocessing, and nothing else — not
    the former label, not the former batch, not when any of it happened.
    """
    return ImportPipelineError(
        SOURCE_PREVIOUSLY_REDACTED,
        "this source was previously imported and then fully forgotten; "
        "re-import it intentionally with --force",
        source_session_id=source_session_id,
    )
