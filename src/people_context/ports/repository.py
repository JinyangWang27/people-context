"""Repository ports: narrow reader/writer Protocols for person persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class PersonReader(Protocol):
    """Read-side access to stored persons.

    The three normalized-name queries are one bounded question asked three ways, over *all* of a
    candidate's identity tokens at once: how many active people do these resolve to, which are the
    first few by name, and is this particular person one of them. They exist because a common name
    can collide with any number of people, and `find_by_normalized_name` answers by materializing
    every one of them — fine for the single-token lookups that predate extraction batches, ruinous
    for a matcher that unions several tokens per candidate and runs once per staged row.
    """

    def get(self, person_id: str) -> Person | None: ...

    def get_self(self) -> Person | None: ...

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]: ...

    def find_by_normalized_name(self, normalized: str) -> list[Person]: ...

    def count_distinct_by_normalized_names(self, normalized: list[str]) -> int: ...

    def page_by_normalized_names(self, normalized: list[str], limit: int) -> list[PersonNameMatch]: ...

    def matches_normalized_names(self, person_id: str, normalized: list[str]) -> bool: ...

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]: ...


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
