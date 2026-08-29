"""Narrow ports for durable trait evidence.

Two capabilities, deliberately separated. Writing a link needs to know what the evidence *is* —
its type and whose record it is — so the subject rule can be enforced before anything is stored.
Reading links back needs the evidence's own sensitivity, because a visible trait must not become
a way to see that a restricted observation exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from people_context.domain.shared import Sensitivity

__all__ = [
    "EvidenceReference",
    "EvidenceRecord",
    "TraitEvidenceLink",
    "TraitEvidenceReader",
    "TraitEvidenceStore",
]


@dataclass(frozen=True)
class EvidenceRecord:
    """What one durable record must disclose for a trait to be allowed to cite it.

    ``person_ids`` is the record's whole subject set — one person for an observation, every
    participant for an interaction — because the ownership rule is the same question in both
    cases: does this record concern the person the trait is about?
    """

    evidence_id: str
    evidence_type: str
    person_ids: tuple[str, ...]
    sensitivity: Sensitivity


@dataclass(frozen=True)
class EvidenceReference:
    """One citation to resolve, carrying the record type when the caller knows it.

    A durable id a caller supplied names no type — it is an opaque token, and the store resolves
    it in the documented order. An id that came from a candidate commit mapping *does* carry one,
    and it matters: ids are unique only within their own table, so a restored store may hold an
    observation and an interaction sharing one, and resolving such a citation by id alone could
    ground the trait in the record the candidate never produced.
    """

    evidence_id: str
    evidence_type: str | None = None


@dataclass(frozen=True)
class TraitEvidenceLink:
    """One persisted trait-to-record citation."""

    trait_id: str
    evidence_type: str
    evidence_id: str
    created_at: datetime


@runtime_checkable
class TraitEvidenceReader(Protocol):
    """Resolve one durable evidence id exactly, without guessing at its format."""

    def get_evidence(self, evidence_id: str, evidence_type: str | None = None) -> EvidenceRecord | None:
        """Return the supported record with exactly this id, or None if there is none.

        ``evidence_type`` narrows the lookup to one record type. Without it the supported types
        are tried in the order the domain declares, which is the only answer available for an
        opaque id whose caller named no type.
        """
        ...


@runtime_checkable
class TraitEvidenceStore(TraitEvidenceReader, Protocol):
    """Read evidence records and persist the links a trait asserts over them."""

    def link_trait_evidence(self, links: Sequence[TraitEvidenceLink]) -> None:
        """Persist every link, ignoring one this trait already asserts."""
        ...
