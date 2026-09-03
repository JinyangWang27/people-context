"""Minimal-disclosure retrieval for a single person's context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from people_context.app.context.models import (
    PersonAffiliationContext,
    PersonRelationshipContext,
    affiliation_context,
    relationship_context,
)
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.person import Person
from people_context.domain.reminder import Reminder, ReminderKind
from people_context.domain.shared import Sensitivity, as_utc
from people_context.domain.trait import Trait
from people_context.ports.clock import Clock
from people_context.ports.context import PersonContextReader
from people_context.ports.repository import PersonReader


class PersonIdentity(BaseModel):
    """The intentionally narrow identity fields exposed by context retrieval."""

    id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    is_self: bool


class TraitEvidenceLink(BaseModel):
    """One returned trait's citation of a durable record it rests on."""

    trait_id: str
    evidence_type: str
    evidence_id: str


class PersonContextResult(BaseModel):
    """Stable response shape for person context, including not-found results.

    ``trait_evidence`` is additive: it explains the traits this bundle already returned by naming
    the records they were drawn from. It carries ids and types only — enough to look the evidence
    up through the ordinary reads, which apply their own disclosure rules — and never any part of
    a record's content.

    ``truncated`` says the shared facts/interactions budget cut the ranked list, matching the field
    the graph and timeline reads already carry. It is computed over the records this caller may
    see, so it never becomes a way to detect that an elevated one exists: a person whose every
    assertion is elevated returns exactly what a person with no assertions returns.
    """

    found: bool
    person_id: str
    identity: PersonIdentity | None = None
    relationships: list[PersonRelationshipContext] = Field(default_factory=list)
    affiliations: list[PersonAffiliationContext] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    trait_evidence: list[TraitEvidenceLink] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class _RankedRecord:
    kind: Literal["fact", "interaction"]
    record: Fact | Interaction
    timestamp: datetime
    confidence: float
    recency: float
    score: float


class GetPersonContext:
    """Assemble bounded context while enforcing purpose and sensitivity rules."""

    def __init__(self, people: PersonReader, context: PersonContextReader, clock: Clock) -> None:
        self._people = people
        self._context = context
        self._clock = clock

    def execute(
        self,
        person_id: str,
        purpose: str | None = None,
        max_items: int = 10,
        include_sensitive: bool = False,
    ) -> PersonContextResult:
        """Return a stable, minimal-disclosure bundle for ``person_id``.

        Facts and interactions share one budget. Eligible records are ordered newest
        first to assign ordinal recency from 1 down to 0, then ranked by
        ``0.7 * recency + 0.3 * confidence`` (interactions use confidence 1.0).
        Score ties break by newest timestamp, record kind, then id.
        """
        if max_items < 0:
            raise ValueError("max_items must be greater than or equal to 0")

        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return PersonContextResult(found=False, person_id=person_id)

        as_of = self._clock.now().date()
        relationships = [
            relationship_context(record) for record in self._context.list_active_relationships(person_id, as_of)
        ]
        affiliations = [
            affiliation_context(record) for record in self._context.list_active_affiliations(person_id, as_of)
        ]
        facts, interactions, truncated = self._rank_disclosure_records(person_id, max_items, include_sensitive)
        traits = self._communication_traits(person_id, purpose, include_sensitive)
        reminders = [
            reminder
            for reminder in self._context.list_active_reminders(person_id)
            if reminder.kind == ReminderKind.COMMUNICATION_NOTE
        ]

        return PersonContextResult(
            found=True,
            person_id=person_id,
            identity=_identity(person),
            relationships=relationships,
            affiliations=affiliations,
            facts=facts,
            interactions=interactions,
            observations=[],
            traits=traits,
            trait_evidence=self._trait_evidence(person_id, traits, include_sensitive),
            reminders=reminders,
            truncated=truncated,
        )

    def _trait_evidence(
        self,
        person_id: str,
        traits: list[Trait],
        include_sensitive: bool,
    ) -> list[TraitEvidenceLink]:
        """Return citations of the returned traits, filtered by the *evidence's* own level.

        Two filters, and they are separate on purpose. A link is only returned for a trait this
        bundle actually disclosed, so evidence cannot explain a trait the caller was not shown.
        And it is returned only if the cited record itself may be disclosed, because a trait's
        level says nothing about its evidence: a personal trait may perfectly well rest on a
        restricted observation, and naming that observation would tell an ordinary caller the
        record exists — which is the disclosure the level exists to prevent.
        """
        disclosed = {trait.id for trait in traits}
        if not disclosed:
            return []
        return [
            TraitEvidenceLink(
                trait_id=record.trait_id,
                evidence_type=record.evidence_type,
                evidence_id=record.evidence_id,
            )
            for record in self._context.list_trait_evidence(person_id)
            if record.trait_id in disclosed and _can_disclose(record.sensitivity, include_sensitive)
        ]

    def _rank_disclosure_records(
        self, person_id: str, max_items: int, include_sensitive: bool
    ) -> tuple[list[Fact], list[Interaction], bool]:
        # Ranking compares instants, so each stored timestamp is normalized to UTC first. The
        # write contract still accepts naive values, and `timestamp()` reads one in the host
        # timezone: without this the same database would rank records differently — and admit
        # different ones at the shared budget's cutoff — depending on the machine's TZ.
        eligible: list[tuple[Literal["fact", "interaction"], Fact | Interaction, datetime, float]] = []
        eligible.extend(
            ("fact", fact, as_utc(fact.recorded_at), fact.confidence)
            for fact in self._context.list_facts(person_id)
            if _can_disclose(fact.sensitivity, include_sensitive)
        )
        eligible.extend(
            ("interaction", interaction, as_utc(interaction.occurred_at), 1.0)
            for interaction in self._context.list_interactions(person_id)
            if _can_disclose(interaction.sensitivity, include_sensitive)
        )
        eligible.sort(key=lambda item: (-item[2].timestamp(), item[0], item[1].id))

        denominator = max(1, len(eligible) - 1)
        ranked = [
            _RankedRecord(
                kind=kind,
                record=record,
                timestamp=timestamp,
                confidence=confidence,
                recency=1.0 if len(eligible) == 1 else 1.0 - index / denominator,
                score=(0.7 * (1.0 if len(eligible) == 1 else 1.0 - index / denominator) + 0.3 * confidence),
            )
            for index, (kind, record, timestamp, confidence) in enumerate(eligible)
        ]
        ranked.sort(key=lambda item: (-item.score, -item.timestamp.timestamp(), item.kind, item.record.id))
        selected = ranked[:max_items]
        facts = [item.record for item in selected if item.kind == "fact" and isinstance(item.record, Fact)]
        interactions = [
            item.record for item in selected if item.kind == "interaction" and isinstance(item.record, Interaction)
        ]
        return facts, interactions, len(ranked) > max_items

    def _communication_traits(self, person_id: str, purpose: str | None, include_sensitive: bool) -> list[Trait]:
        if purpose is None or "communication" not in purpose.casefold():
            return []
        return [
            trait
            for trait in self._context.list_traits(person_id)
            if _can_disclose(trait.sensitivity, include_sensitive)
        ]


def _identity(person: Person) -> PersonIdentity:
    return PersonIdentity(
        id=person.id,
        canonical_name=person.canonical_name,
        aliases=[alias.value for alias in person.aliases],
        summary=person.summary,
        is_self=person.is_self,
    )


def _can_disclose(sensitivity: Sensitivity, include_sensitive: bool) -> bool:
    return include_sensitive or sensitivity in (Sensitivity.PUBLIC, Sensitivity.PERSONAL)

