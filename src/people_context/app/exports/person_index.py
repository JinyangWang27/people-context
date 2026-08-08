"""Deterministic person index for integrations that address people by stable id.

The index exists so a consumer never has to address a person by display name: it pairs each
stable person id with just enough identity to render a list, and nothing else. It is a read
path — it records nothing and mints no audit or changelog rows — and it stays at ordinary
identity level, carrying no facts, interactions, traits, or reminders at any sensitivity.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonReader

PERSON_INDEX_FORMAT = "people-context-person-index"
PERSON_INDEX_VERSION = 1


class PersonIndexEntry(BaseModel):
    """One person's stable id and the identity fields needed to list them."""

    id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None
    is_self: bool = False
    # Lifecycle state is explicit rather than inferred from the entry's presence, because the
    # index only contains soft-deleted people when they were asked for.
    deleted: bool = False


class PersonIndexDocument(BaseModel):
    """The versioned person index; a declared machine interface under the M12 promise."""

    format: str = PERSON_INDEX_FORMAT
    version: int = PERSON_INDEX_VERSION
    generated_at: datetime
    include_deleted: bool = False
    people: list[PersonIndexEntry] = Field(default_factory=list)


class ListPersonIndex:
    """Project the stored people into one stably ordered index document."""

    def __init__(self, people: PersonReader, clock: Clock) -> None:
        self._people = people
        self._clock = clock

    def execute(self, *, include_deleted: bool = False, limit: int | None = None) -> PersonIndexDocument:
        """Return the index for the same people the human listing would show.

        `limit` bounds how many people are read, exactly as it does for the human listing;
        the entries that survive it are then ordered by normalized name and id, so the
        document does not depend on the reader's own collation.
        """
        people = self._people.list_people(include_deleted=include_deleted, limit=limit)
        entries = sorted(
            (_entry(person) for person in people),
            key=lambda entry: (normalize_name(entry.canonical_name), entry.id),
        )
        return PersonIndexDocument(
            generated_at=self._clock.now(),
            include_deleted=include_deleted,
            people=entries,
        )


def render_person_index_json(document: PersonIndexDocument) -> str:
    """Render the versioned machine document as canonical JSON text."""
    return render_json_document(document)


def _entry(person: Person) -> PersonIndexEntry:
    return PersonIndexEntry(
        id=person.id,
        canonical_name=person.canonical_name,
        aliases=[alias.value for alias in person.aliases],
        summary=person.summary,
        is_self=person.is_self,
        deleted=person.deleted_at is not None,
    )
