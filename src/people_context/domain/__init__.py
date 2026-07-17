"""Domain entities and value objects (pure, no I/O)."""

from __future__ import annotations

from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY, CommunicationPhilosophy
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderKind, ReminderStatus
from people_context.domain.shared import (
    Confidence,
    Provenance,
    Sensitivity,
    ValidityPeriod,
    new_id,
    normalize_name,
    utc_now,
)
from people_context.domain.trait import Trait, TraitCategory

__all__ = [
    "PREF_COMMUNICATION_PHILOSOPHY",
    "Affiliation",
    "Alias",
    "AliasKind",
    "CommunicationPhilosophy",
    "Confidence",
    "Fact",
    "Interaction",
    "Observation",
    "Organization",
    "Person",
    "Provenance",
    "Reminder",
    "ReminderKind",
    "ReminderStatus",
    "Relationship",
    "Sensitivity",
    "Trait",
    "TraitCategory",
    "ValidityPeriod",
    "new_id",
    "normalize_name",
    "utc_now",
]
