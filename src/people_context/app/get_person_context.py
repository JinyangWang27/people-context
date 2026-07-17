"""Minimal-disclosure retrieval for a single person's context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from people_context.app.person_context_models import (
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
from people_context.domain.shared import Sensitivity
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


class PersonContextResult(BaseModel):
    """Stable response shape for person context, including not-found results."""

    found: bool
    person_id: str
    identity: PersonIdentity | None = None
    relationships: list[PersonRelationshipContext] = Field(default_factory=list)
    affiliations: list[PersonAffiliationContext] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)


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
        facts, interactions = self._rank_disclosure_records(person_id, max_items, include_sensitive)
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
            reminders=reminders,
        )

    def _rank_disclosure_records(
        self, person_id: str, max_items: int, include_sensitive: bool
    ) -> tuple[list[Fact], list[Interaction]]:
        eligible: list[tuple[Literal["fact", "interaction"], Fact | Interaction, datetime, float]] = []
        eligible.extend(
            ("fact", fact, fact.recorded_at, fact.confidence)
            for fact in self._context.list_facts(person_id)
            if _can_disclose(fact.sensitivity, include_sensitive)
        )
        eligible.extend(
            ("interaction", interaction, interaction.occurred_at, 1.0)
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
        return facts, interactions

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
