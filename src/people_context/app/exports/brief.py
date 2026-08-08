"""Deterministic single-person brief for surfaces that cannot call the MCP server.

A brief is a read-only projection. It records nothing, mints no audit or changelog rows,
and adds no model-callable tool: it composes three existing reads into one document that a
person can paste into a note or an integration can parse.

Disclosure is deliberately asymmetric and is labelled rather than implied. `include_sensitive`
widens only the context-backed records — facts, interactions, and the purpose-gated traits —
because that is the one place `GetPersonContext` accepts the flag. Communication guidance keeps
its own ordinary-disclosure contract in both modes, so a brief taken with the flag still shows
guidance built from `public`/`personal` records only. Once written, the document is outside the
server's disclosure controls entirely, which the notice in every rendering says out loud.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from people_context.app.context.guidance import GetCommunicationGuidance
from people_context.app.context.models import PersonAffiliationContext, PersonRelationshipContext
from people_context.app.context.query import GetPersonContext, PersonIdentity
from people_context.app.exports._document import render_json_document
from people_context.app.records.reminders import ListReminders, ListRemindersInput
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.domain.shared import normalize_name
from people_context.domain.trait import Trait
from people_context.ports.clock import Clock

BRIEF_FORMAT = "people-context-brief"
BRIEF_VERSION = 1

# The brief asks for communication context explicitly, because that purpose is what makes
# `GetPersonContext` return traits at all.
BRIEF_PURPOSE = "communication"

# The context budget is pinned here rather than inherited from the use case default, so the
# document keeps its size when that default changes.
BRIEF_CONTEXT_ITEMS = 10

DISCLOSURE_NOTICE = (
    "This brief is a local export and is outside the server's disclosure controls. "
    "Everything it contains is plaintext personal data once rendered or written."
)


class DisclosureLevel(StrEnum):
    """How much of the store one section of a brief was allowed to read."""

    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"


class BriefDisclosure(BaseModel):
    """The labelled disclosure state of one composed brief."""

    include_sensitive: bool = False
    context: DisclosureLevel = DisclosureLevel.ORDINARY
    # Guidance is ordinary in both modes; the constant field states that in the document
    # rather than leaving a reader to infer it from `include_sensitive`.
    guidance: DisclosureLevel = DisclosureLevel.ORDINARY
    notice: str = DISCLOSURE_NOTICE


class BriefGuidance(BaseModel):
    """Ordinary-disclosure communication signal, never widened by `include_sensitive`."""

    disclosure: DisclosureLevel = DisclosureLevel.ORDINARY
    traits: dict[str, list[Trait]] = Field(default_factory=dict)
    friction_notes: list[str] = Field(default_factory=list)
    communication_philosophy: str | None = None


class PersonBriefDocument(BaseModel):
    """One person's versioned brief; a declared machine interface under the M12 promise."""

    format: str = BRIEF_FORMAT
    version: int = BRIEF_VERSION
    generated_at: datetime
    disclosure: BriefDisclosure
    person: PersonIdentity
    relationships: list[PersonRelationshipContext] = Field(default_factory=list)
    affiliations: list[PersonAffiliationContext] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    traits: list[Trait] = Field(default_factory=list)
    reminders: list[Reminder] = Field(default_factory=list)
    guidance: BriefGuidance = Field(default_factory=BriefGuidance)


class ComposePersonBrief:
    """Compose context, communication guidance, and reminders into one stable document.

    `ListReminders` is required rather than convenient: neither of the other two reads returns
    `follow_up` or `occasion` rows, so without it a brief would silently show only communication
    notes.

    Every people-linked collection is ordered by a key ending in a record id, so the document
    never depends on the order a reader happened to return rows in.
    """

    def __init__(
        self,
        context: GetPersonContext,
        guidance: GetCommunicationGuidance,
        reminders: ListReminders,
        clock: Clock,
    ) -> None:
        self._context = context
        self._guidance = guidance
        self._reminders = reminders
        self._clock = clock

    def execute(self, person_id: str, *, include_sensitive: bool = False) -> PersonBriefDocument | None:
        """Return the brief for one active person, or `None` when there is nothing to brief on."""
        context = self._context.execute(
            person_id,
            purpose=BRIEF_PURPOSE,
            max_items=BRIEF_CONTEXT_ITEMS,
            include_sensitive=include_sensitive,
        )
        if not context.found or context.identity is None:
            return None
        # Guidance is called without the flag at all: its use case has no such parameter, and
        # that is exactly the ordinary-disclosure contract the brief labels and preserves.
        guidance = self._guidance.execute(person_id)
        reminders = self._reminders.execute(
            ListRemindersInput(person_id=person_id, status=ReminderStatus.ACTIVE)
        )
        return PersonBriefDocument(
            generated_at=self._clock.now(),
            disclosure=BriefDisclosure(
                include_sensitive=include_sensitive,
                context=DisclosureLevel.SENSITIVE if include_sensitive else DisclosureLevel.ORDINARY,
            ),
            person=context.identity,
            relationships=sorted(context.relationships, key=_relationship_key),
            affiliations=sorted(context.affiliations, key=_affiliation_key),
            # Facts and interactions keep the relevance ranking `GetPersonContext` already
            # imposes, which is a total order down to the record id.
            facts=list(context.facts),
            interactions=list(context.interactions),
            traits=sorted(context.traits, key=_trait_key),
            reminders=sorted(reminders, key=_reminder_key),
            guidance=BriefGuidance(
                traits={
                    category: sorted(items, key=_trait_key)
                    for category, items in guidance.traits.items()
                },
                friction_notes=list(guidance.friction_notes),
                communication_philosophy=guidance.communication_philosophy,
            ),
        )


def render_brief_json(document: PersonBriefDocument) -> str:
    """Render the versioned machine document as canonical JSON text."""
    return render_json_document(document)


def render_brief_markdown(document: PersonBriefDocument) -> str:
    """Render the same document as human-readable Markdown.

    The Markdown layout is deterministic but deliberately not frozen; integrations read the
    JSON form instead. Empty sections are still emitted, so the section order a reader scans
    is the same for every person.
    """
    person = document.person
    lines = [f"# {person.canonical_name}", ""]
    lines.extend(
        [
            f"- **Person id:** {person.id}",
            f"- **Generated:** {document.generated_at.isoformat()}",
            f"- **Self:** {'yes' if person.is_self else 'no'}",
            f"- **Aliases:** {', '.join(person.aliases) if person.aliases else '(none)'}",
            f"- **Summary:** {person.summary or '(none)'}",
            f"- **Context disclosure:** {document.disclosure.context.value}",
            f"- **Guidance disclosure:** {document.disclosure.guidance.value} (never widened)",
            "",
            f"> {document.disclosure.notice}",
            "",
        ]
    )
    lines.extend(
        _section(
            "Relationships",
            [
                f"{record.display_type}: {record.other_person_name} ({record.other_person_id})"
                + (f" — {record.relationship.label}" if record.relationship.label else "")
                for record in document.relationships
            ],
        )
    )
    lines.extend(
        _section(
            "Affiliations",
            [f"{record.affiliation.role} at {record.organization_name}" for record in document.affiliations],
        )
    )
    lines.extend(_section("Facts", [f"{fact.predicate}: {fact.value}" for fact in document.facts]))
    lines.extend(
        _section(
            "Interactions",
            [
                f"{interaction.occurred_at.date().isoformat()}: {interaction.summary}"
                for interaction in document.interactions
            ],
        )
    )
    lines.extend(_section("Traits", [f"{trait.category.value}: {trait.value}" for trait in document.traits]))
    lines.extend(_section("Reminders", [_reminder_line(reminder) for reminder in document.reminders]))

    guidance = document.guidance
    lines.append(f"## Communication guidance ({guidance.disclosure.value} disclosure)")
    lines.append("")
    lines.append(f"Philosophy: {guidance.communication_philosophy or '(none set)'}")
    lines.append("")
    lines.extend(
        _section(
            "Guidance traits",
            [
                f"{category}: {trait.value}"
                for category, traits in guidance.traits.items()
                for trait in traits
            ],
            level=3,
        )
    )
    lines.extend(_section("Recent interaction notes", list(guidance.friction_notes), level=3))
    return "\n".join(lines).rstrip("\n") + "\n"


def _section(title: str, items: list[str], level: int = 2) -> list[str]:
    """Render one Markdown section, keeping an empty one visible rather than dropping it."""
    body = [f"- {item}" for item in items] if items else ["_None recorded._"]
    return ["#" * level + f" {title}", "", *body, ""]


def _reminder_line(reminder: Reminder) -> str:
    due = "no due date" if reminder.due_at is None else f"due {reminder.due_at.isoformat()}"
    recurrence = f", repeats {reminder.recurrence}" if reminder.recurrence else ""
    return f"{reminder.kind.value} ({due}{recurrence}): {reminder.text}"


def _relationship_key(record: PersonRelationshipContext) -> tuple[str, str, str, str]:
    return (
        record.display_type,
        normalize_name(record.other_person_name),
        record.other_person_id,
        record.relationship.id,
    )


def _affiliation_key(record: PersonAffiliationContext) -> tuple[str, str, str]:
    return (
        normalize_name(record.organization_name),
        normalize_name(record.affiliation.role),
        record.affiliation.id,
    )


def _trait_key(trait: Trait) -> tuple[str, str, str]:
    return (trait.category.value, trait.value, trait.id)


def _reminder_key(reminder: Reminder) -> tuple[bool, str, str, str]:
    """Order dated reminders before undated ones without inventing a timezone.

    The write contract still accepts a naive `due_at`, and `datetime` refuses to compare a
    naive value with an aware one, so an instant-based key would raise on a mixed store.
    Calling `timestamp()` on a naive value instead would read it in the host timezone and make
    the order depend on the machine. Comparing the stored ISO spelling avoids both: it is total,
    host-independent, and chronological for the aware, UTC-offset timestamps every supported
    write path produces.
    """
    return (
        reminder.due_at is None,
        "" if reminder.due_at is None else reminder.due_at.isoformat(),
        reminder.kind.value,
        reminder.id,
    )
