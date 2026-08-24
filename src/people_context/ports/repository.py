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


@runtime_checkable
class PersonReader(Protocol):
    """Read-side access to stored persons."""

    def get(self, person_id: str) -> Person | None: ...

    def get_self(self) -> Person | None: ...

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]: ...

    def find_by_normalized_name(self, normalized: str) -> list[Person]: ...

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
