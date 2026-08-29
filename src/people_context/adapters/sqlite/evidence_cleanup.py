"""Erasing the trait-evidence links that name records hard forget actually removes.

The relation has two ends and forget can arrive at either. Erasing a trait removes the links it
asserts; erasing an observation or an interaction removes every link that cites it, whichever
trait — and whoever's trait — that is.

The rule the second half turns on is *actually removes*. A person-scope forget deletes the
interactions that person was alone in and leaves the shared ones standing, so a link from
somebody else's trait to a surviving interaction is not touched. Only a link whose record has
genuinely gone is a link that would dangle.

Discovery is structural throughout: links are found by id, in the two columns that hold ids, and
nothing here reads or matches any record's text.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from people_context.domain.trait_evidence import TRAIT_EVIDENCE_TYPES, trait_evidence_key
from people_context.ports.lifecycle import AffectedEntity


@dataclass(frozen=True)
class EvidenceCleanupPlan:
    """Exactly which trait-evidence links an erasure removes, computed without mutating."""

    links: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """The per-relation count for forget previews and results.

        A database with no evidence links reports nothing rather than a zero, so an ordinary
        forget keeps the deletion summary it always had.
        """
        return {"trait_evidence": len(self.links)} if self.links else {}

    @property
    def affected_entities(self) -> list[AffectedEntity]:
        """Return the removed links as affected entities, so peer replay erases them too.

        The identity is the one `RecordTrait` journalled the link under — both sides read the same
        helper — which is what makes this forget's audit and changelog redaction able to find that
        history, and what keeps two links that differ only by evidence type distinguishable.
        """
        return [
            AffectedEntity(entity_type="trait_evidence", entity_id=trait_evidence_key(*link))
            for link in self.links
        ]


class TraitEvidenceCleaner:
    """Remove the evidence links belonging to, or citing, entities a forget erases."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def plan(self, entity_targets: Sequence[tuple[str, str]]) -> EvidenceCleanupPlan:
        """Compute the whole removal without touching a row."""
        links: set[tuple[str, str, str]] = set()
        for entity_type, entity_id in entity_targets:
            if entity_type == "trait":
                links.update(self._links_of_trait(entity_id))
            elif entity_type in TRAIT_EVIDENCE_TYPES:
                links.update(self._links_citing(entity_type, entity_id))
        return EvidenceCleanupPlan(links=sorted(links))

    def erase(self, entity_targets: Sequence[tuple[str, str]]) -> EvidenceCleanupPlan:
        """Delete the planned links inside the caller's forget transaction."""
        plan = self.plan(entity_targets)
        for chunk in _chunks(plan.links):
            self._conn.executemany(
                """DELETE FROM trait_evidence
                   WHERE trait_id = ? AND evidence_type = ? AND evidence_id = ?""",
                chunk,
            )
        return plan

    def _links_of_trait(self, trait_id: str) -> Iterable[tuple[str, str, str]]:
        rows = self._conn.execute(
            "SELECT trait_id, evidence_type, evidence_id FROM trait_evidence WHERE trait_id = ?",
            (trait_id,),
        ).fetchall()
        return [(row["trait_id"], row["evidence_type"], row["evidence_id"]) for row in rows]

    def _links_citing(self, evidence_type: str, evidence_id: str) -> Iterable[tuple[str, str, str]]:
        rows = self._conn.execute(
            """SELECT trait_id, evidence_type, evidence_id FROM trait_evidence
               WHERE evidence_type = ? AND evidence_id = ?""",
            (evidence_type, evidence_id),
        ).fetchall()
        return [(row["trait_id"], row["evidence_type"], row["evidence_id"]) for row in rows]


def _chunks(
    values: list[tuple[str, str, str]],
    size: int = 500,
) -> Iterable[list[tuple[str, str, str]]]:
    """Split deletions into statement-sized batches, as every other bulk path here does."""
    for start in range(0, len(values), size):
        yield values[start : start + size]
