"""Identity-resolution use case: rank stored persons against a name query."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from people_context.domain.person import Alias, Person
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock
from people_context.ports.context import PersonContextReader
from people_context.ports.repository import PersonReader

#: `match_reason` of a candidate found only by bounded edit distance. It is the one stage that
#: matches a name the store does not actually contain, so callers that act on a single candidate
#: treat it as a suggestion to confirm rather than an identification.
FUZZY_MATCH_REASON = "fuzzy"

#: `match_reason` of a candidate whose stored name or alias equals the query once normalized.
EXACT_MATCH_REASON = "exact"

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
    match_detail: str | None = None
    aliases: list[str] = Field(default_factory=list)
    summary: str | None = None


class ResolutionResult(BaseModel):
    """The ranked outcome of resolving a query, with an ambiguity flag."""

    query: str
    candidates: list[ResolutionCandidate]
    ambiguous: bool


class ResolutionHints(BaseModel):
    """Optional organization, role, and relationship context for re-ranking."""

    model_config = ConfigDict(extra="forbid")

    org: str | None = None
    role: str | None = None
    relationship: str | None = None


def _candidate(
    person: Person,
    score: float,
    match_reason: str,
    match_detail: str | None = None,
) -> ResolutionCandidate:
    return ResolutionCandidate(
        person_id=person.id,
        canonical_name=person.canonical_name,
        score=score,
        match_reason=match_reason,
        match_detail=match_detail,
        aliases=[alias.value for alias in person.aliases],
        summary=person.summary,
    )


def _alias_detail_order(alias: Alias) -> tuple[str, str]:
    """Order matching aliases by kind string then alias id, so detail is deterministic."""
    return (str(alias.kind), alias.id)


def _exact_match_detail(person: Person, normalized_query: str) -> str | None:
    """Describe which stored name produced an exact normalized match.

    Canonical always wins when it matches. Otherwise the matching alias with the
    lowest ``(kind, id)`` pair supplies ``alias:<kind>``. The value is descriptive
    rather than a closed enum, so callers must tolerate unfamiliar strings.
    """
    if normalize_name(person.canonical_name) == normalized_query:
        return "canonical_name"
    matching = [alias for alias in person.aliases if normalize_name(alias.value) == normalized_query]
    if not matching:
        return None
    return f"alias:{min(matching, key=_alias_detail_order).kind}"


class ResolvePerson:
    """Resolve a free-text name query to ranked candidate persons."""

    def __init__(
        self,
        reader: PersonReader,
        context_reader: PersonContextReader | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._reader = reader
        self._context_reader = context_reader
        self._clock = clock

    def execute(self, query: str, limit: int = 5, hints: ResolutionHints | None = None) -> ResolutionResult:
        """Run exact, search, and guarded fuzzy stages before ranking candidates."""
        best: dict[str, ResolutionCandidate] = {}
        normalized_query = normalize_name(query)

        exact_people = self._reader.find_by_normalized_name(normalized_query)
        for person in exact_people:
            self._offer(best, _candidate(person, 1.0, "exact", _exact_match_detail(person, normalized_query)))

        strongest_search_score = 0.0
        for hit in self._reader.search_names(query, limit=limit):
            score = 0.4 + 0.4 * hit.score
            strongest_search_score = max(strongest_search_score, score)
            self._offer(best, _candidate(hit.person, score, f"search:{hit.match_kind}"))

        if not exact_people and len(normalized_query) >= _MIN_FUZZY_QUERY_LENGTH and strongest_search_score < 0.5:
            for person in self._reader.list_people():
                distances = (
                    _bounded_levenshtein(normalized_query, normalize_name(name), max_distance=2)
                    for name in person.all_names()
                )
                distance = min(distances, default=3)
                if distance in _FUZZY_SCORES:
                    self._offer(best, _candidate(person, _FUZZY_SCORES[distance], FUZZY_MATCH_REASON))

        if hints is not None and self._context_reader is not None and self._clock is not None:
            best = {person_id: self._boost_with_hints(candidate, hints) for person_id, candidate in best.items()}

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

    def _boost_with_hints(self, candidate: ResolutionCandidate, hints: ResolutionHints) -> ResolutionCandidate:
        context_reader = self._context_reader
        clock = self._clock
        if context_reader is None or clock is None:
            return candidate
        as_of = clock.now().date()
        affiliations = context_reader.list_active_affiliations(candidate.person_id, as_of)
        relationships = context_reader.list_active_relationships(candidate.person_id, as_of)
        matched_kinds: list[str] = []

        if hints.org and any(_substring_match(hints.org, record.organization_name) for record in affiliations):
            matched_kinds.append("org")
        if hints.role and any(_substring_match(hints.role, record.affiliation.role) for record in affiliations):
            matched_kinds.append("role")
        if hints.relationship and any(
            _substring_match(hints.relationship, value)
            for record in relationships
            for value in (record.relationship.type, record.relationship.label)
            if value
        ):
            matched_kinds.append("relationship")

        if not matched_kinds:
            return candidate
        score = 1.0 if candidate.score == 1.0 else min(0.99, candidate.score + 0.15 * len(matched_kinds))
        suffix = "".join(f"+hint:{kind}" for kind in matched_kinds)
        return candidate.model_copy(update={"score": score, "match_reason": candidate.match_reason + suffix})


def base_match_reason(match_reason: str) -> str:
    """Return the stage that produced a candidate, with any hint suffixes removed.

    A matched hint appends `+hint:<kind>` and adds 0.15 to the score, so neither the score nor an
    equality test on the whole reason tells you which stage found the candidate: two matched hints
    lift a 0.45 edit-distance guess to 0.75 under the reason `fuzzy+hint:org+hint:role`. Callers
    deciding whether a single candidate is an identification ask this, not the score.
    """
    return match_reason.split("+", 1)[0]


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


def _substring_match(hint: str, value: str) -> bool:
    normalized_hint = normalize_name(hint)
    normalized_value = normalize_name(value)
    return bool(normalized_hint and normalized_value) and (
        normalized_hint in normalized_value or normalized_value in normalized_hint
    )
