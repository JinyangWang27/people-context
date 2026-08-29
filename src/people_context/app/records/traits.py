"""Record a derived trait about an existing person."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.app._mutation import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.app.records.trait_evidence import resolve_trait_evidence
from people_context.domain.shared import Confidence, Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.evidence import EvidenceRecord, TraitEvidenceLink, TraitEvidenceStore
from people_context.ports.records import RecordWriter
from people_context.ports.repository import PersonReader


class RecordTraitInput(BaseModel):
    """Input for a derived trait assertion.

    ``evidence_ids`` names durable observations and interactions this inference rests on. It is
    additive and optional: `evidence_note` remains the human-readable account of the reasoning,
    and a trait with neither is exactly as valid as it was before evidence links existed.
    """

    person_id: str
    category: TraitCategory
    value: str
    evidence_note: str | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    evidence_ids: list[str] = Field(default_factory=list)
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordTrait:
    """Create one provenanced trait for a known person, with the evidence it cites.

    ``evidence`` is optional so that every caller wired before M18.3 keeps working unchanged. A
    request that cites evidence without one is a wiring mistake rather than a user error — the
    links would be silently dropped, leaving a trait that claims grounding it does not have — so
    it raises rather than degrading.
    """

    def __init__(
        self,
        people: PersonReader,
        writer: RecordWriter,
        audit: AuditLog,
        clock: Clock,
        evidence: TraitEvidenceStore | None = None,
    ) -> None:
        self._people = people
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._evidence = evidence
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: RecordTraitInput, *, transaction_id: str | None = None) -> Trait:
        """Persist and audit a validated trait category and its evidence links.

        Evidence is resolved before the trait is written, so a citation that cannot be honoured
        refuses the whole assertion rather than storing a trait whose grounding silently went
        missing. Both writes share the caller's transaction, so they roll back together.
        """
        require_active_person(self._people, data.person_id)
        evidence = self._resolve(data)
        trait = Trait(
            person_id=data.person_id,
            category=data.category,
            value=data.value,
            evidence_note=data.evidence_note,
            confidence=data.confidence if data.confidence is not None else 1.0,
            sensitivity=data.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
            updated_at=self._clock.now(),
        )
        self._writer.save_trait(trait)
        # The seam mints a transaction id when the caller supplies none, and this is one logical
        # mutation: the trait and the links that ground it must reach a peer as one group, or a
        # replay could apply the trait without its evidence. Reusing what the first call returned
        # is what keeps that true on the direct path as well as through an import commit.
        transaction_id = audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="trait",
            entity_id=trait.id,
            payload=snapshot(trait),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
            transaction_id=transaction_id,
        )
        self._link(trait, evidence, data, transaction_id)
        return trait

    def _resolve(self, data: RecordTraitInput) -> list[EvidenceRecord]:
        if not data.evidence_ids:
            return []
        if self._evidence is None:
            raise RuntimeError("recording trait evidence requires a trait evidence store")
        return resolve_trait_evidence(self._evidence, data.person_id, data.evidence_ids)

    def _link(
        self,
        trait: Trait,
        evidence: list[EvidenceRecord],
        data: RecordTraitInput,
        transaction_id: str | None,
    ) -> None:
        """Persist and journal each citation as the durable relation it is.

        A link is replicable primary state, so it is accountable like any other durable write:
        one audit and changelog effect per link, sharing the trait's own transaction. The
        composite entity id is what lets hard forget find and redact this history later, exactly
        as it does for an interaction's participants.

        The replay image carries `created_at` while the accountability payload does not. That
        asymmetry is the same one every other primary write here makes: a consumer *applies* the
        replay image, so it must contain every column the row requires, and `trait_evidence`
        stores its creation instant as `NOT NULL`.
        """
        if not evidence:
            return
        if self._evidence is None:  # pragma: no cover - guarded by `_resolve`
            return
        now = self._clock.now()
        self._evidence.link_trait_evidence(
            [
                TraitEvidenceLink(
                    trait_id=trait.id,
                    evidence_type=record.evidence_type,
                    evidence_id=record.evidence_id,
                    created_at=now,
                )
                for record in evidence
            ]
        )
        for record in evidence:
            audit_mutation(
                self._audit,
                self._clock,
                op="create",
                entity_type="trait_evidence",
                entity_id=f"{trait.id}:{record.evidence_id}",
                payload={
                    "trait_id": trait.id,
                    "evidence_type": record.evidence_type,
                    "evidence_id": record.evidence_id,
                },
                replay_payload={
                    "trait_id": trait.id,
                    "evidence_type": record.evidence_type,
                    "evidence_id": record.evidence_id,
                    "created_at": now.isoformat(),
                },
                changed_fields=["created_at", "evidence_id", "evidence_type", "trait_id"],
                source=data.source,
                session=data.session,
                stated_by=data.stated_by,
                transaction_id=transaction_id,
            )
