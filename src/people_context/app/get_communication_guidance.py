"""Assemble deterministic signal for client-composed communication advice."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.app.get_person_context import (
    PersonAffiliationContext,
    PersonRelationshipContext,
    _affiliation_context,
    _relationship_context,
)
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.reminder import Reminder, ReminderKind
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import Trait, TraitCategory
from people_context.ports.clock import Clock
from people_context.ports.context import PersonContextReader
from people_context.ports.records import PreferencesStore
from people_context.ports.repository import PersonReader

DEFAULT_FRICTION_NOTES_LIMIT = 5


class CommunicationGuidanceResult(BaseModel):
    """Stable guidance bundle, including a not-found shape."""

    found: bool
    person_id: str
    situation: str | None = None
    traits: dict[str, list[Trait]] = Field(default_factory=dict)
    relationships: list[PersonRelationshipContext] = Field(default_factory=list)
    affiliations: list[PersonAffiliationContext] = Field(default_factory=list)
    friction_notes: list[str] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)
    communication_philosophy: str | None = None
    philosophy_set: bool = False


class GetCommunicationGuidance:
    """Return sensitivity-gated signal without generating advice."""

    def __init__(
        self,
        people: PersonReader,
        context: PersonContextReader,
        preferences: PreferencesStore,
        clock: Clock,
    ) -> None:
        self._people = people
        self._context = context
        self._preferences = preferences
        self._clock = clock

    def execute(
        self,
        person_id: str,
        situation: str | None = None,
        friction_notes_limit: int = DEFAULT_FRICTION_NOTES_LIMIT,
    ) -> CommunicationGuidanceResult:
        """Assemble communication signal for one known person."""
        person = self._people.get(person_id)
        if person is None:
            return CommunicationGuidanceResult(found=False, person_id=person_id, situation=situation)
        as_of = self._clock.now().date()
        traits = self._group_traits(person_id)
        interactions = sorted(
            (
                interaction
                for interaction in self._context.list_interactions(person_id)
                if _can_disclose(interaction.sensitivity)
            ),
            key=lambda interaction: (-interaction.occurred_at.timestamp(), interaction.id),
        )
        reminders = [
            reminder
            for reminder in self._context.list_active_reminders(person_id)
            if reminder.kind == ReminderKind.COMMUNICATION_NOTE
        ]
        philosophy = self._preferences.get(PREF_COMMUNICATION_PHILOSOPHY)
        return CommunicationGuidanceResult(
            found=True,
            person_id=person_id,
            situation=situation,
            traits=traits,
            relationships=[
                _relationship_context(record) for record in self._context.list_active_relationships(person_id, as_of)
            ],
            affiliations=[
                _affiliation_context(record) for record in self._context.list_active_affiliations(person_id, as_of)
            ],
            friction_notes=[interaction.summary for interaction in interactions[:friction_notes_limit]],
            reminders=reminders,
            communication_philosophy=philosophy,
            philosophy_set=philosophy is not None,
        )

    def _group_traits(self, person_id: str) -> dict[str, list[Trait]]:
        grouped: dict[str, list[Trait]] = {}
        traits = self._context.list_traits(person_id)
        for category in TraitCategory:
            category_traits = [
                trait
                for trait in traits
                if trait.category == category and _can_disclose(trait.sensitivity)
            ]
            if category_traits:
                grouped[category.value] = category_traits
        return grouped


def _can_disclose(sensitivity: Sensitivity) -> bool:
    return sensitivity in (Sensitivity.PUBLIC, Sensitivity.PERSONAL)
