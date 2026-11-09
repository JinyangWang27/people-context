"""Repository ports: narrow reader/writer Protocols for person persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from people_context.domain.person import Person


@dataclass(frozen=True)
class SearchHit:
    """A ranked name-search result."""

    person: Person
    score: float  # 0..1
    matched_value: str  # the name/alias string that matched
    match_kind: str  # "canonical" | "alias"


@dataclass(frozen=True)
class PersonNameMatch:
    """One active person an identity token resolved to, carrying only what naming it needs.

    Deliberately not a `Person`: the three queries below exist so that a name shared by thousands
    of people costs a bounded read, and returning whole records would put the unbounded load back
    where it was removed from.
    """

    id: str
    canonical_name: str


@dataclass(frozen=True)
class PersonNameMatches:
    """How many active people a set of identity tokens resolves to, and the first of them.

    The two travel together because deciding between "one person" and "a question nobody has
    answered" needs both, and reading them separately makes the decision a race: a second person
    created between the count and the selection turns an ambiguity into a confident wrong answer.
    One value from one statement cannot disagree with itself.

    ``first`` is the lowest by canonical name then id, and is ``None`` only when ``total`` is 0.
    """

    total: int
    first: PersonNameMatch | None


@runtime_checkable
class PersonReader(Protocol):
    """Read-side access to stored persons.

    The three normalized-name queries are one bounded question asked three ways, over *all* of a
    candidate's identity tokens at once: how many active people do these resolve to and which is
    the first, which are the first few by name, and is this particular person one of them. They
    exist because a common name can collide with any number of people, and
    `find_by_normalized_name` answers by materializing every one of them — fine for the
    single-token lookups that predate extraction batches, ruinous for a matcher that unions
    several tokens per candidate and runs once per staged row.
    """

    def get(self, person_id: str) -> Person | None: ...

    def get_self(self) -> Person | None: ...

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]: ...

    def find_by_normalized_name(self, normalized: str) -> list[Person]: ...

    def match_normalized_names(self, normalized: list[str]) -> PersonNameMatches: ...

    def page_by_normalized_names(self, normalized: list[str], limit: int) -> list[PersonNameMatch]: ...

    def matches_normalized_names(self, person_id: str, normalized: list[str]) -> bool: ...

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]: ...


#: The orders a person-index page may be read in; the two timestamps run newest first.
PersonPageSort = Literal["name", "created", "updated"]


@dataclass(frozen=True)
class PersonPageQuery:
    """What one person-index page is narrowed to and ordered by; the default is every active person by name.

    ``text`` is a normalized substring matched against the canonical name, every alias, and the
    summary. ``group_id`` honours membership and group sensitivity: unless ``include_sensitive``,
    only public and personal memberships of a public or personal group count.
    """

    text: str | None = None
    sort: PersonPageSort = "name"
    organization_id: str | None = None
    group_id: str | None = None
    include_sensitive: bool = False


@dataclass(frozen=True)
class PersonFilterOption:
    """One organization or group a person-index page can be narrowed to."""

    id: str
    name: str


class PersonIndexReader(Protocol):
    """The person-index read: the whole listing, or one keyset page of active people.

    ``page_people`` orders by normalized canonical name then id, and resumes after the person
    ``after_person_id`` names — soft-deleted or not, since its row still fixes a position. It
    returns ``None`` when no row has that id any more, so a caller can refuse a stale cursor
    instead of silently restarting at the top. ``get`` lets the caller check the anchor has not
    moved in the order since the cursor was issued. ``where`` narrows and orders the page; the
    order it names replaces the name order above. ``person_filter_options`` lists the organizations
    and groups worth offering as filters: those with at least one active member the view may show.
    """

    def get(self, person_id: str) -> Person | None: ...

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]: ...

    def page_people(
        self, *, limit: int, after_person_id: str | None, where: PersonPageQuery | None = None
    ) -> list[Person] | None: ...

    def person_filter_options(
        self, *, include_sensitive: bool
    ) -> tuple[list[PersonFilterOption], list[PersonFilterOption]]: ...


@runtime_checkable
class PersonWriter(Protocol):
    """Write-side access to stored persons."""

    def save_person(self, person: Person) -> None: ...


@runtime_checkable
class PersonSearchIndexer(Protocol):
    """Rebuild the derived active-person name index atomically."""

    def rebuild_person_search(self) -> tuple[int, int]: ...


@runtime_checkable
class PeopleRepository(PersonReader, PersonWriter, PersonSearchIndexer, Protocol):
    """The whole person-persistence surface, for callers that need every side of it.

    The narrow ports above stay the right dependency for a use case that only reads or
    only writes. A decorator that wraps person persistence as a unit is a different case:
    naming the three ports as a union would let a read-only object satisfy the annotation
    and then fail at runtime on the first write, so the composition is stated once here.
    """
