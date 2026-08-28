"""The durable vocabulary of import provenance: receipt metadata and staged references.

Two things live here because two layers must agree on them exactly.

**Receipt metadata rules.** `source_kind` is a machine category, and hard forget relies on that:
it deliberately keeps the kind on a terminal redacted receipt while scrubbing every caller-authored
field around it. That retention is only safe while the field genuinely cannot hold a person or a
title, so the rule has to hold at *every* boundary that can create a receipt — the staging commands
and a restored bundle alike. A restore that accepted `Interview with Alice` as a kind would let
erased wording survive the one field erasure is designed to preserve.

**Staged-candidate references.** After staging rewrites a caller's batch-local refs, the persisted
candidate names other candidates through a small set of canonical id fields. Commit indexes them
directly and hard forget walks them to find dependents, so both the restore validator and the
erasure logic read this one declaration rather than each keeping its own copy.

Everything here raises plain ``ValueError``. The process boundary wraps it in the refusal its own
callers expect; the bundle models let Pydantic report it.
"""

from __future__ import annotations

import re
from typing import Any, Final

from people_context.domain.staged_candidate import staged_candidate_error

#: Characters a bounded machine source category may carry.
MAX_SOURCE_KIND_CHARS: Final = 128

#: Characters an optional caller-authored label or external source id may carry.
MAX_SOURCE_LABEL_CHARS: Final = 256

#: Characters an optional extraction-contract revision identifier may carry.
MAX_CONTRACT_REVISION_CHARS: Final = 64

#: A conservative machine-identifier alphabet: a source class or adapter name, not a title.
SOURCE_KIND_PATTERN: Final = re.compile(r"\A[A-Za-z0-9._/-]+\Z")

#: The same idea one character narrower — a revision identifier names no hierarchy.
CONTRACT_REVISION_PATTERN: Final = re.compile(r"\A[A-Za-z0-9._-]+\Z")

#: A SHA-256 digest or extraction fingerprint, in exactly one accepted spelling.
HEX64_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z")

#: Separator inside a composed claim key. A unit separator cannot occur in a source kind, a hex
#: digest, or the absence sentinel, so the composition is unambiguous.
CLAIM_KEY_SEPARATOR: Final = "\x1f"

#: Stands in for "the caller supplied no extraction fingerprint" inside a canonical claim key.
#:
#: SQLite treats NULLs in a UNIQUE index as distinct, so a nullable fingerprint column would let
#: two "digest present, fingerprint absent" sessions each claim the same source. Composing the
#: key with a fixed sentinel makes absence one stable state instead. The value is deliberately
#: not 64 hexadecimal characters, so it cannot collide with any real fingerprint.
EXTRACTION_FINGERPRINT_ABSENT: Final = "fingerprint-absent"

#: Statuses a persisted staging row may carry.
STAGING_STATUSES: Final[tuple[str, ...]] = ("pending", "committed")

#: Receipt statuses that still own reviewable rows.
#:
#: A receipt's status is recomputed from its rows at every commit, so this is what decides whether
#: staging travels: the bundle reader selects rows by it, and restore validation refuses a receipt
#: whose status and rows disagree. Both read this one declaration, because a receipt that exported
#: no rows but was validated as if it had would drop a candidate from the next bundle in silence.
REVIEWABLE_SESSION_STATUSES: Final[tuple[str, ...]] = ("staged", "partially_committed")

#: Candidate types the stager persists.
STAGED_CANDIDATE_TYPES: Final[frozenset[str]] = frozenset(
    {"person", "interaction", "affiliation", "fact", "observation", "trait", "relationship"}
)

#: Canonical fields naming exactly one other candidate in the same batch.
STAGED_REFERENCE_FIELDS: Final[tuple[str, ...]] = (
    "person_candidate_id",
    "from_candidate_id",
    "to_candidate_id",
)

#: Canonical fields naming a list of other candidates in the same batch.
STAGED_REFERENCE_LIST_FIELDS: Final[tuple[str, ...]] = (
    "participant_candidate_ids",
    "evidence_candidate_ids",
)

#: Canonical fields naming durable records rather than batch-local candidates.
STAGED_DURABLE_REFERENCE_FIELDS: Final[tuple[str, ...]] = ("evidence_ids",)

#: What each staged type must carry for commit to resolve it. Commit indexes these directly, so a
#: row missing one is not a candidate commit can decline — it is a row that would raise.
REQUIRED_STAGED_REFERENCES: Final[dict[str, tuple[str, ...]]] = {
    "person": (),
    "interaction": ("participant_candidate_ids",),
    "affiliation": ("person_candidate_id",),
    "fact": ("person_candidate_id",),
    "observation": ("person_candidate_id",),
    "trait": ("person_candidate_id",),
    "relationship": ("from_candidate_id", "to_candidate_id"),
}


def check_staged_candidate(candidate: dict[str, Any]) -> str:
    """Return the accepted persisted candidate's type, or raise ``ValueError``.

    The whole shape is checked, through the strict models in `domain/staged_candidate.py`, because
    neither way of being wrong has a good report at commit time. A row missing a field commit
    indexes raises mid-transaction, after earlier candidates in the same commit have written; a
    structurally wrong value fails the same way at its durable write, and until then review shows
    whatever it contains. Both are refused here, where the batch can still be declined whole.

    The reference fields are checked first and by hand, because their requirement is not a
    property of one candidate: they name *other rows in the same batch*, and the message that
    names the missing one is what the batch-local closure elsewhere reports against.
    """
    candidate_type = candidate.get("type")
    if candidate_type not in STAGED_CANDIDATE_TYPES:
        raise ValueError("staged candidate must carry a supported type")
    kind = str(candidate_type)
    for field_name in REQUIRED_STAGED_REFERENCES[kind]:
        value = candidate.get(field_name)
        resolved = {value} if isinstance(value, str) and value else identifier_list(value)
        if not resolved:
            raise ValueError(f"staged {kind} candidate must carry {field_name}")
    reason = staged_candidate_error(candidate)
    if reason is not None:
        raise ValueError(f"staged {kind} candidate is not a valid persisted candidate: {reason}")
    return kind


def check_source_kind(value: str) -> str:
    """Return the accepted machine category, or raise ``ValueError``."""
    kind = value.strip()
    if not kind:
        raise ValueError("must not be blank")
    if len(kind) > MAX_SOURCE_KIND_CHARS:
        raise ValueError(f"is at most {MAX_SOURCE_KIND_CHARS} characters")
    if not SOURCE_KIND_PATTERN.match(kind):
        raise ValueError("must be a machine category of ASCII letters, digits, '.', '_', '-', or '/'")
    return kind


def check_opaque_label(value: str) -> str:
    """Return one bounded opaque caller string, or raise ``ValueError``.

    Opaque means opaque: an external identifier belongs to another system, so nothing here
    case-folds or otherwise rewrites it. Only surrounding whitespace is removed.
    """
    text = value.strip()
    if len(text) > MAX_SOURCE_LABEL_CHARS:
        raise ValueError(f"is at most {MAX_SOURCE_LABEL_CHARS} characters")
    return text


def check_hex64(value: str) -> str:
    """Return one accepted SHA-256 digest or extraction fingerprint, or raise ``ValueError``."""
    text = value.strip()
    if not HEX64_PATTERN.match(text):
        raise ValueError("must be exactly 64 lowercase hexadecimal characters")
    return text


def check_contract_revision(value: str) -> str:
    """Return one accepted extraction-contract revision identifier, or raise ``ValueError``."""
    text = value.strip()
    if len(text) > MAX_CONTRACT_REVISION_CHARS:
        raise ValueError(f"is at most {MAX_CONTRACT_REVISION_CHARS} characters")
    if not CONTRACT_REVISION_PATTERN.match(text):
        raise ValueError("must be ASCII letters, digits, '.', '_', or '-'")
    return text


def staged_candidate_references(candidate: dict[str, Any]) -> set[str]:
    """Return every batch-local candidate id one persisted candidate canonically names."""
    references = {
        value
        for field_name in STAGED_REFERENCE_FIELDS
        if isinstance(value := candidate.get(field_name), str)
    }
    for field_name in STAGED_REFERENCE_LIST_FIELDS:
        references |= identifier_list(candidate.get(field_name))
    return references


def identifier_list(value: Any) -> set[str]:
    """Return the identifier strings of a canonical reference list, ignoring anything else."""
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def compose_claim_key(source_kind: str, content_digest: str | None, extraction_fingerprint: str | None) -> str | None:
    """Return the canonical duplicate claim key, or ``None`` when the source asserts none.

    A digestless session deliberately has no canonical claim: People Context was never given
    bytes it could identify the source by, so ``(source_kind, null, null)`` would be a fake
    claim that suppressed later legitimate staging of similar material.
    """
    if content_digest is None:
        return None
    fingerprint = extraction_fingerprint or EXTRACTION_FINGERPRINT_ABSENT
    return CLAIM_KEY_SEPARATOR.join((source_kind, content_digest, fingerprint))
