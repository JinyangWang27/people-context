"""Identity-resolution use case: rank stored persons against a name query."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.domain.person import Person
from people_context.domain.shared import normalize_name
from people_context.ports.repository import PersonReader

_MIN_SCORE = 0.35
_AMBIGUOUS_GAP = 0.2
_MIN_FUZZY_QUERY_LENGTH = 3
_FUZZY_SCORES = {1: 0.45, 2: 0.38}


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
        """Run exact, search, and guarded fuzzy stages before ranking candidates."""
        best: dict[str, ResolutionCandidate] = {}
        normalized_query = normalize_name(query)

        exact_people = self._reader.find_by_normalized_name(normalized_query)
        for person in exact_people:
            self._offer(best, _candidate(person, 1.0, "exact"))

        strongest_search_score = 0.0
        for hit in self._reader.search_names(query, limit=limit):
            score = 0.4 + 0.4 * hit.score
            strongest_search_score = max(strongest_search_score, score)
            self._offer(best, _candidate(hit.person, score, f"search:{hit.match_kind}"))

        if (
            not exact_people
            and len(normalized_query) >= _MIN_FUZZY_QUERY_LENGTH
            and strongest_search_score < 0.5
        ):
            for person in self._reader.list_people():
                distances = (
                    _bounded_levenshtein(normalized_query, normalize_name(name), max_distance=2)
                    for name in person.all_names()
                )
                distance = min(distances, default=3)
                if distance in _FUZZY_SCORES:
                    self._offer(best, _candidate(person, _FUZZY_SCORES[distance], "fuzzy"))

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


def _bounded_levenshtein(left: str, right: str, max_distance: int) -> int:
    """Return edit distance up to ``max_distance``, or one greater when exceeded."""
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1
    if left == right:
        return 0

    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        row_minimum = left_index
        for right_index, right_character in enumerate(right, start=1):
            current_value = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            )
            current.append(current_value)
            row_minimum = min(row_minimum, current_value)
        if row_minimum > max_distance:
            return max_distance + 1
        previous = current

    distance = previous[-1]
    return distance if distance <= max_distance else max_distance + 1
