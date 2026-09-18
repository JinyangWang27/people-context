"""Deterministic person index for integrations that address people by stable id.

The index exists so a consumer never has to address a person by display name: it pairs each
stable person id with just enough identity to render a list, and nothing else. It is a read
path — it records nothing and mints no audit or changelog rows — and it stays at ordinary
identity level, carrying no facts, interactions, traits, or reminders at any sensitivity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.domain.source_cursor import decode_cursor, encode_cursor
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonIndexReader

PERSON_INDEX_FORMAT = "people-context-person-index"
PERSON_INDEX_VERSION = 1

#: Scope of a cursor issued by the paged person index.
PERSON_INDEX_SCOPE: Final = "people"

#: The page a cursor request carries when the caller names no limit, and the bounds it may ask
#: for. They apply only to paged reads: an unpaged `execute` keeps its own unbounded contract.
DEFAULT_PERSON_PAGE_LIMIT: Final = 50
MIN_PERSON_PAGE_LIMIT: Final = 1
MAX_PERSON_PAGE_LIMIT: Final = 200

#: Stable refusals for a paged read.
INVALID_PERSON_PAGE_LIMIT: Final = "invalid_person_page_limit"
INVALID_PERSON_CURSOR: Final = "invalid_person_cursor"


class PersonIndexError(ValueError):
    """Raised when a paged person-index argument is refused; ``code`` is stable."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


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
    # Additive (M30.1): set only by a paged read when more people remain after this page.
    next_cursor: str | None = None


class ListPersonIndex:
    """Project the stored people into one stably ordered index document."""

    def __init__(self, people: PersonIndexReader, clock: Clock) -> None:
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

    def page(self, *, limit: int = DEFAULT_PERSON_PAGE_LIMIT, cursor: str | None = None) -> PersonIndexDocument:
        """Return one bounded page of active people, and where the next page resumes.

        Pages are ordered by normalized name and id, the same order `execute` sorts into, and
        continue by keyset rather than offset, so following `next_cursor` reaches every person
        exactly once. The cursor names the last person returned, encoded like the import-sources
        cursor; one naming a person that no longer exists is refused, not restarted.
        """
        if not MIN_PERSON_PAGE_LIMIT <= limit <= MAX_PERSON_PAGE_LIMIT:
            raise PersonIndexError(
                f"limit must be between {MIN_PERSON_PAGE_LIMIT} and {MAX_PERSON_PAGE_LIMIT}",
                code=INVALID_PERSON_PAGE_LIMIT,
            )
        after = None
        if cursor is not None:
            try:
                after = decode_cursor(cursor, scope=PERSON_INDEX_SCOPE)
            except ValueError as exc:
                raise PersonIndexError(str(exc), code=INVALID_PERSON_CURSOR) from None
        rows = self._people.page_people(limit=limit + 1, after_person_id=after)
        if rows is None:
            raise PersonIndexError("this cursor no longer names a person", code=INVALID_PERSON_CURSOR)
        page = rows[:limit]
        return PersonIndexDocument(
            generated_at=self._clock.now(),
            people=[_entry(person) for person in page],
            next_cursor=encode_cursor(PERSON_INDEX_SCOPE, page[-1].id) if len(rows) > limit else None,
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
