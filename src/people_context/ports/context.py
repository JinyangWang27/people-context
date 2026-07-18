"""Read-side port for assembling person context from existing records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder
from people_context.domain.trait import Trait


@dataclass(frozen=True)
class RelationshipRecord:
    """A relationship hydrated with the other endpoint's identity and perspective type."""

    relationship: Relationship
    other_person_id: str
    other_person_name: str
    display_type: str | None = None


@dataclass(frozen=True)
class AffiliationRecord:
    """An affiliation hydrated with its organization name."""

    affiliation: Affiliation
    organization_name: str


@runtime_checkable
class PersonContextReader(Protocol):
    """Read all existing record types needed by retrieval use cases."""

    def list_active_relationships(self, person_id: str, as_of: date) -> list[RelationshipRecord]: ...

    def list_active_affiliations(self, person_id: str, as_of: date) -> list[AffiliationRecord]: ...

    def list_facts(self, person_id: str) -> list[Fact]: ...

    def list_observations(self, person_id: str) -> list[Observation]: ...

    def list_traits(self, person_id: str) -> list[Trait]: ...

    def list_interactions(self, person_id: str) -> list[Interaction]: ...

    def list_active_reminders(self, person_id: str) -> list[Reminder]: ...
