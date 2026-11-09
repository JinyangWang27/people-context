"""Deterministic person index for integrations that address people by stable id.

The index exists so a consumer never has to address a person by display name: it pairs each
stable person id with just enough identity to render a list, and nothing else. It is a read
path — it records nothing and mints no audit or changelog rows — and it stays at ordinary
identity level, carrying no facts, interactions, traits, or reminders at any sensitivity.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Final, cast, get_args

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.domain.person import Person, contact_emails
from people_context.domain.shared import normalize_name
from people_context.domain.source_cursor import decode_cursor, encode_cursor
from people_context.ports.clock import Clock
from people_context.ports.repository import PersonFilterOption, PersonIndexReader, PersonPageQuery, PersonPageSort

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
INVALID_PERSON_SORT: Final = "invalid_person_sort"

#: The orders a page may be read in: by name, or newest first by when a person was added or last edited.
PERSON_PAGE_SORTS: Final[tuple[str, ...]] = get_args(PersonPageSort)


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
    # Additive: the email addresses among `aliases`, which are contact information rather than names.
    emails: list[str] = Field(default_factory=list)
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

    def page(
        self,
        *,
        limit: int = DEFAULT_PERSON_PAGE_LIMIT,
        cursor: str | None = None,
        query: str | None = None,
        sort: str = "name",
        organization_id: str | None = None,
        group_id: str | None = None,
        include_sensitive: bool = False,
    ) -> PersonIndexDocument:
        """Return one bounded page of active people, and where the next page resumes.

        By default pages are ordered by normalized name and id, the same order `execute` sorts
        into; `sort` may instead name `created` or `updated`, newest first. `query` keeps the
        people whose name, an alias, or the summary contains it, compared normalized;
        `organization_id` and `group_id` keep the people affiliated with that organization or in
        that group, a group counting only memberships the view may show unless `include_sensitive`.
        Pages continue by keyset rather than offset, so following `next_cursor` with the same
        arguments reaches every matching person exactly once. The cursor names the last person
        returned and the order it was issued in, encoded like the import-sources cursor; one naming
        a person that no longer exists, or issued for another order, is refused, not restarted.
        """
        if not MIN_PERSON_PAGE_LIMIT <= limit <= MAX_PERSON_PAGE_LIMIT:
            raise PersonIndexError(
                f"limit must be between {MIN_PERSON_PAGE_LIMIT} and {MAX_PERSON_PAGE_LIMIT}",
                code=INVALID_PERSON_PAGE_LIMIT,
            )
        if sort not in PERSON_PAGE_SORTS:
            raise PersonIndexError(f"sort must be one of {', '.join(PERSON_PAGE_SORTS)}", code=INVALID_PERSON_SORT)
        text = normalize_name(query) if query else ""
        where = PersonPageQuery(
            text=text or None,
            sort=cast(PersonPageSort, sort),
            organization_id=organization_id or None,
            group_id=group_id or None,
            include_sensitive=include_sensitive,
        )
        after = None if cursor is None else self._anchor(cursor, sort)
        rows = self._people.page_people(limit=limit + 1, after_person_id=after, where=where)
        if rows is None:
            raise PersonIndexError("this cursor no longer names a person", code=INVALID_PERSON_CURSOR)
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            anchor = page[-1]
            next_cursor = encode_cursor(_scope(sort), _key_digest(anchor, sort) + anchor.id)
        return PersonIndexDocument(
            generated_at=self._clock.now(),
            people=[_entry(person) for person in page],
            next_cursor=next_cursor,
        )

    def filter_options(self, *, include_sensitive: bool) -> dict[str, list[dict[str, str]]]:
        """Return the organizations and groups a page can be narrowed to, each by id and name."""
        organizations, groups = self._people.person_filter_options(include_sensitive=include_sensitive)

        def options(rows: list[PersonFilterOption]) -> list[dict[str, str]]:
            return [{"id": row.id, "name": row.name} for row in rows]

        return {"organizations": options(organizations), "groups": options(groups)}

    def _anchor(self, cursor: str, sort: str) -> str:
        """Return the id a cursor resumes after, refusing one whose person moved in the order.

        The store resolves the anchor's *current* position, so a rename (or, for a timestamp
        order, an edit) between two pages would resume from the wrong place and skip or repeat
        people. The cursor therefore also carries a digest of the key it was issued under, and a
        changed key refuses rather than continues.
        """
        try:
            key = decode_cursor(cursor, scope=_scope(sort))
        except ValueError as exc:
            raise PersonIndexError(str(exc), code=INVALID_PERSON_CURSOR) from None
        digest, person_id = key[:_NAME_DIGEST_CHARS], key[_NAME_DIGEST_CHARS:]
        anchor = self._people.get(person_id) if person_id else None
        if anchor is None:
            raise PersonIndexError("this cursor no longer names a person", code=INVALID_PERSON_CURSOR)
        if digest != _key_digest(anchor, sort):
            raise PersonIndexError(
                "the list changed since this page; start again from the first page", code=INVALID_PERSON_CURSOR
            )
        return person_id


def render_person_index_json(document: PersonIndexDocument) -> str:
    """Render the versioned machine document as canonical JSON text."""
    return render_json_document(document)


#: Fixed width of the name digest that opens every person-index cursor key.
_NAME_DIGEST_CHARS: Final = 16


def _scope(sort: str) -> str:
    """Return the cursor scope of one order; the name order keeps the scope it always had."""
    return PERSON_INDEX_SCOPE if sort == "name" else f"{PERSON_INDEX_SCOPE}:{sort}"


def _key_digest(person: Person, sort: str) -> str:
    """Return a fixed-width fingerprint of the ordering key a cursor was issued under."""
    if sort == "name":
        key = normalize_name(person.canonical_name)
    else:
        key = (person.created_at if sort == "created" else person.updated_at).isoformat()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_NAME_DIGEST_CHARS]


def _entry(person: Person) -> PersonIndexEntry:
    return PersonIndexEntry(
        id=person.id,
        canonical_name=person.canonical_name,
        aliases=[alias.value for alias in person.aliases],
        summary=person.summary,
        is_self=person.is_self,
        emails=contact_emails(person.aliases),
        deleted=person.deleted_at is not None,
    )
