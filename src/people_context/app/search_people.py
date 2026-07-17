"""Search use case: thin ranking over the repository's name search."""

from __future__ import annotations

from people_context.app.resolve_person import ResolutionCandidate
from people_context.ports.repository import PersonReader


class SearchPeople:
    """Rank stored persons for a name query, reusing the resolution candidate DTO."""

    def __init__(self, reader: PersonReader) -> None:
        self._reader = reader

    def execute(self, query: str, limit: int = 10) -> list[ResolutionCandidate]:
        """Return ranked candidates straight from the repository search."""
        return [
            ResolutionCandidate(
                person_id=hit.person.id,
                canonical_name=hit.person.canonical_name,
                score=hit.score,
                match_reason=f"search:{hit.match_kind}",
                aliases=[alias.value for alias in hit.person.aliases],
                summary=hit.person.summary,
            )
            for hit in self._reader.search_names(query, limit=limit)
        ]
