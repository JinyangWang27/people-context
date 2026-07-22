"""Immutable fictional seed data for the packaged demonstration database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime


@dataclass(frozen=True)
class DemoPersonSeed:
    """One fictional person in the packaged demo."""

    key: str
    name: str
    handles: tuple[str, ...]
    summary: str
    is_self: bool = False


@dataclass(frozen=True)
class DemoAffiliationSeed:
    """One fictional professional affiliation."""

    person_key: str
    organization: str
    role: str


@dataclass(frozen=True)
class DemoFactSeed:
    """One fictional time-aware fact."""

    person_key: str
    predicate: str
    value: str
    valid_from: date | None = None


@dataclass(frozen=True)
class DemoInteractionSeed:
    """One fictional summary-only interaction."""

    summary: str
    participant_keys: tuple[str, ...]
    occurred_at: datetime
    channel: str


@dataclass(frozen=True)
class DemoRelationshipSeed:
    """One fictional relationship edge."""

    subject_key: str
    object_key: str
    relationship_type: str


DEMO_PEOPLE: tuple[DemoPersonSeed, ...] = (
    DemoPersonSeed(
        key="self",
        name="Maya Chen",
        handles=("maya.chen@example.test",),
        summary="Product lead coordinating a community technology program.",
        is_self=True,
    ),
    DemoPersonSeed(
        key="amina",
        name="Amina Hassan",
        handles=("amina.hassan@example.test",),
        summary="Research partner focused on accessible public services.",
    ),
    DemoPersonSeed(
        key="daniel",
        name="Daniel Okafor",
        handles=("daniel.okafor@example.test",),
        summary="Engineering manager for the shared delivery team.",
    ),
    DemoPersonSeed(
        key="sofia",
        name="Sofia Alvarez",
        handles=("sofia.alvarez@example.test",),
        summary="Community organizer and workshop facilitator.",
    ),
)

DEMO_AFFILIATIONS: tuple[DemoAffiliationSeed, ...] = (
    DemoAffiliationSeed("self", "Civic Loom", "Product Lead"),
    DemoAffiliationSeed("amina", "Open City Lab", "Researcher"),
    DemoAffiliationSeed("daniel", "Civic Loom", "Engineering Manager"),
    DemoAffiliationSeed("sofia", "Neighborhood Commons", "Program Director"),
)

DEMO_FACTS: tuple[DemoFactSeed, ...] = (
    DemoFactSeed("amina", "location", "Dubai"),
    DemoFactSeed("daniel", "preferred_update_style", "Concise written updates"),
    DemoFactSeed("sofia", "workshop_focus", "Inclusive facilitation", date(2026, 1, 1)),
)

DEMO_INTERACTIONS: tuple[DemoInteractionSeed, ...] = (
    DemoInteractionSeed(
        "Quarterly planning session",
        ("self", "amina", "daniel"),
        datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
        "in-person",
    ),
    DemoInteractionSeed(
        "Community workshop follow-up",
        ("amina", "sofia"),
        datetime(2026, 6, 18, 14, 30, tzinfo=UTC),
        "video",
    ),
)

DEMO_RELATIONSHIPS: tuple[DemoRelationshipSeed, ...] = (
    DemoRelationshipSeed("self", "daniel", "colleague_of"),
    DemoRelationshipSeed("daniel", "amina", "colleague_of"),
    DemoRelationshipSeed("amina", "sofia", "friend_of"),
)
