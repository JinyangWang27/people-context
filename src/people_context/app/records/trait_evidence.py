"""Deciding whether a trait may cite a durable record, and in what order.

Two callers need this answer and they need it for opposite reasons. `RecordTrait` owns the write
and must refuse an impossible citation outright. `CommitImport` must *decline* to write, leaving
the candidate committable later, because an evidence record it cannot resolve yet is the ordinary
state of a batch part-way through its commits. Both ask the same three questions, so both ask
them here rather than each keeping its own copy that could drift.

The questions are:

- does a record with exactly this id exist? Ids are opaque, so this is a lookup and never a
  pattern match;
- is it a type a trait may cite? Only observations and interactions are; a trait citing a trait
  would be a belief chain;
- does it concern the trait's own subject? Without this an unrelated trait becomes a way to learn
  that a record about somebody else exists.
"""

from __future__ import annotations

from collections.abc import Sequence

from people_context.domain.trait_evidence import TRAIT_EVIDENCE_TYPES
from people_context.ports.evidence import EvidenceRecord, EvidenceReference, TraitEvidenceReader


class TraitEvidenceError(Exception):
    """One trait's evidence cannot be linked, with a stable machine reason.

    ``evidence_id`` names the id that failed. That is the caller's own input echoed back — an
    identifier they supplied — and never any part of the record it addresses.
    """

    def __init__(self, code: str, message: str, evidence_id: str) -> None:
        self.code = code
        self.evidence_id = evidence_id
        super().__init__(message)


def resolve_trait_evidence(
    reader: TraitEvidenceReader,
    person_id: str,
    references: Sequence[EvidenceReference],
) -> list[EvidenceRecord]:
    """Return the citable records for ``references``, in stable order, or raise.

    A reference resolved from a candidate commit mapping carries the record type that candidate
    produced, and it is honoured: ids are unique only within their own table, so resolving such a
    citation by id alone could answer with a different record that happens to share the id.

    Ordering is by `(type, id)` rather than by the order the caller listed them, so the links one
    trait produces — and therefore the rows a later read returns — do not depend on how an agent
    happened to arrange its request. Duplicates collapse on the *resolved* record: citing one
    record twice asserts the same single link.
    """
    resolved: dict[tuple[str, str], EvidenceRecord] = {}
    for reference in references:
        evidence_id = reference.evidence_id
        record = reader.get_evidence(evidence_id, reference.evidence_type)
        if record is None:
            raise TraitEvidenceError(
                "evidence_not_found",
                "trait evidence must name an existing observation or interaction",
                evidence_id,
            )
        if record.evidence_type not in TRAIT_EVIDENCE_TYPES:
            raise TraitEvidenceError(
                "unsupported_evidence_type",
                "a trait may only cite an observation or an interaction",
                evidence_id,
            )
        if person_id not in record.person_ids:
            raise TraitEvidenceError(
                "evidence_subject_mismatch",
                "trait evidence must concern the person the trait is about",
                evidence_id,
            )
        resolved[(record.evidence_type, record.evidence_id)] = record
    return [resolved[key] for key in sorted(resolved)]
