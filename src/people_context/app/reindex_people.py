"""Rebuild the derived active-person full-text index."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.ports.repository import PersonSearchIndexer


class ReindexPeopleResult(BaseModel):
    """Counts from one atomic person-only FTS rebuild."""

    people: int
    names: int


class ReindexPeople:
    """Delegate a full derived-index rebuild to the persistence port."""

    def __init__(self, indexer: PersonSearchIndexer) -> None:
        self._indexer = indexer

    def execute(self) -> ReindexPeopleResult:
        people, names = self._indexer.rebuild_person_search()
        return ReindexPeopleResult(people=people, names=names)
