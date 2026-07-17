"""In-memory fake implementations of the ports (proves Liskov substitutability).

These are plain Python objects backed by dicts/lists that structurally satisfy the
PersonReader / PersonWriter / AuditLog / Clock Protocols, so use cases can be exercised
without the SQLite adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime

from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.audit_log import AuditEntry
from people_context.ports.repository import SearchHit


class FakeClock:
    """A clock returning a fixed, advanceable time."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FakeAuditLog:
    """An in-memory append-only audit log."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def append(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def list_entries(self, limit: int = 100) -> list[AuditEntry]:
        return list(reversed(self.entries))[:limit]


class FakePeopleRepository:
    """An in-memory PersonReader + PersonWriter backed by a dict.

    `search_names` does containment matching with a length-ratio score by default.
    Tests can force exact hits per query via `forced_hits` to control scores precisely.
    """

    def __init__(self) -> None:
        self._people: dict[str, Person] = {}
        self.forced_hits: dict[str, list[SearchHit]] = {}

    # -- writer ------------------------------------------------------------

    def save_person(self, person: Person) -> None:
        self._people[person.id] = person

    # -- reader ------------------------------------------------------------

    def get(self, person_id: str) -> Person | None:
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return None
        return person

    def get_self(self) -> Person | None:
        for person in self._people.values():
            if person.is_self and person.deleted_at is None:
                return person
        return None

    def list_people(self, include_deleted: bool = False, limit: int | None = None) -> list[Person]:
        people = [p for p in self._people.values() if include_deleted or p.deleted_at is None]
        people.sort(key=lambda p: p.canonical_name)
        return people[:limit] if limit is not None else people

    def find_by_normalized_name(self, normalized: str) -> list[Person]:
        matches: list[Person] = []
        for person in self._people.values():
            if person.deleted_at is not None:
                continue
            names = {normalize_name(name) for name in person.all_names()}
            if normalized in names:
                matches.append(person)
        return matches

    def search_names(self, query: str, limit: int = 10) -> list[SearchHit]:
        if query in self.forced_hits:
            return self.forced_hits[query][:limit]

        normalized_query = normalize_name(query)
        hits: list[SearchHit] = []
        for person in self._people.values():
            if person.deleted_at is not None:
                continue
            hit = self._best_containment(person, normalized_query)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda h: (-h.score, h.person.canonical_name))
        return hits[:limit]

    @staticmethod
    def _best_containment(person: Person, normalized_query: str) -> SearchHit | None:
        best: SearchHit | None = None
        candidates = [(person.canonical_name, "canonical"), *((a.value, "alias") for a in person.aliases)]
        for value, kind in candidates:
            normalized_value = normalize_name(value)
            if normalized_query and normalized_query in normalized_value:
                score = min(1.0, len(normalized_query) / len(normalized_value))
                if best is None or score > best.score:
                    best = SearchHit(person=person, score=score, matched_value=value, match_kind=kind)
        return best
