"""Identity-resolution use case: rank stored persons against a name query."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.repository import PersonReader

_MIN_SCORE = 0.35
_AMBIGUOUS_GAP = 0.2


class ResolutionCandidate(BaseModel):
    """A single ranked match for a resolution query."""

    person_id: str
    canonical_name: str
    score: float
    match_reason: str
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None


class ResolutionResult(BaseModel):
    """The ranked outcome of resolving a query, with an ambiguity flag."""

    query: str
    candidates: list[ResolutionCandidate]
    ambiguous: bool


def _candidate(person: Person, score: float, match_reason: str) -> ResolutionCandidate:
    return ResolutionCandidate(
        person_id=person.id,
        canonical_name=person.canonical_name,
        score=score,
        match_reason=match_reason,
        aliases=[alias.value for alias in person.aliases],
        summary=person.summary,
    )


class ResolvePerson:
    """Resolve a free-text name query to ranked candidate persons."""

    def __init__(self, reader: PersonReader) -> None:
        self._reader = reader

    def execute(self, query: str, limit: int = 5) -> ResolutionResult:
        """Run the exact + search stages, dedupe, threshold, sort, and truncate."""
        best: dict[str, ResolutionCandidate] = {}

        for person in self._reader.find_by_normalized_name(normalize_name(query)):
            self._offer(best, _candidate(person, 1.0, "exact"))

        for hit in self._reader.search_names(query, limit=limit):
            score = 0.4 + 0.4 * hit.score
            self._offer(best, _candidate(hit.person, score, f"search:{hit.match_kind}"))

        candidates = [c for c in best.values() if c.score >= _MIN_SCORE]
        candidates.sort(key=lambda c: (-c.score, c.canonical_name))
        candidates = candidates[:limit]

        ambiguous = len(candidates) >= 2 and (candidates[0].score - candidates[1].score) < _AMBIGUOUS_GAP
        return ResolutionResult(query=query, candidates=candidates, ambiguous=ambiguous)

    @staticmethod
    def _offer(best: dict[str, ResolutionCandidate], candidate: ResolutionCandidate) -> None:
        existing = best.get(candidate.person_id)
        if existing is None or candidate.score > existing.score:
            best[candidate.person_id] = candidate
