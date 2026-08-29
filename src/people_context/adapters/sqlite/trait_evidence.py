"""SQLite adapter for durable trait evidence.

Two operations, both narrow. Resolving one caller-supplied evidence id is an exact lookup — no
pattern, no case folding, no assumption that the id is a generated ULID — so a restored `obs-1`
stays as addressable as anything this installation minted. Persisting links is an idempotent
insert, because citing the same record twice is the caller saying one thing twice, not an error.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from people_context.domain.shared import Sensitivity
from people_context.domain.trait_evidence import TRAIT_EVIDENCE_TYPES
from people_context.ports.evidence import EvidenceRecord, TraitEvidenceLink


class SqliteTraitEvidenceStore:
    """Resolve supported evidence records and persist trait citations over them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        """Return the observation or interaction with exactly this id, or None.

        The two tables are consulted in the fixed order the domain declares. Generated ids are
        ULIDs and never collide across tables, but a restored or hand-authored id need not be
        one, and a stable order is what keeps such an id resolving to the same record on every
        machine rather than to whichever table happened to be read first.
        """
        for evidence_type in TRAIT_EVIDENCE_TYPES:
            record = self._lookup(evidence_type, evidence_id)
            if record is not None:
                return record
        return None

    def link_trait_evidence(self, links: Sequence[TraitEvidenceLink]) -> None:
        """Persist every link, leaving an already-asserted one untouched."""
        self._conn.executemany(
            """INSERT INTO trait_evidence (trait_id, evidence_type, evidence_id, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(trait_id, evidence_type, evidence_id) DO NOTHING""",
            [
                (link.trait_id, link.evidence_type, link.evidence_id, link.created_at.isoformat())
                for link in links
            ],
        )

    def list_links(self, trait_id: str) -> list[TraitEvidenceLink]:
        """Return one trait's citations in stable `(type, id)` order."""
        rows = self._conn.execute(
            """SELECT trait_id, evidence_type, evidence_id, created_at
               FROM trait_evidence WHERE trait_id = ?
               ORDER BY evidence_type, evidence_id""",
            (trait_id,),
        ).fetchall()
        return [
            TraitEvidenceLink(
                trait_id=row["trait_id"],
                evidence_type=row["evidence_type"],
                evidence_id=row["evidence_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def _lookup(self, evidence_type: str, evidence_id: str) -> EvidenceRecord | None:
        if evidence_type == "observation":
            row = self._conn.execute(
                "SELECT person_id, sensitivity FROM observations WHERE id = ?",
                (evidence_id,),
            ).fetchone()
            if row is None:
                return None
            return EvidenceRecord(
                evidence_id=evidence_id,
                evidence_type="observation",
                person_ids=(row["person_id"],),
                sensitivity=Sensitivity(row["sensitivity"]),
            )
        row = self._conn.execute(
            "SELECT sensitivity FROM interactions WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            return None
        participants = self._conn.execute(
            "SELECT person_id FROM interaction_participants WHERE interaction_id = ? ORDER BY person_id",
            (evidence_id,),
        ).fetchall()
        return EvidenceRecord(
            evidence_id=evidence_id,
            evidence_type="interaction",
            person_ids=tuple(participant["person_id"] for participant in participants),
            sensitivity=Sensitivity(row["sensitivity"]),
        )
