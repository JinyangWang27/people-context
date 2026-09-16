"""Deterministic person-candidate identity matching for extraction batches.

The released staging matcher answers one question — "is there an existing person to attach
this to?" — and reports only an id or nothing. That collapses two different situations into
the same `matched_person_id=null`: nobody matched, and several people matched. The first is a
new identity; the second is a question nobody has answered yet, and committing it as a new
person is how a duplicate gets created out of an ambiguity.

Extraction batches keep the two apart. Matching takes the union of the active people every
identity token resolves to, so a unique hit on one token cannot mask a conflict on another,
and the resulting disposition — not the presence of an id — is what commit acts on.

That union is computed by the reader, in one bounded query over all of a candidate's tokens,
rather than by loading every colliding `Person` and unioning them here. The two answers are the
same; only the cost differs, and it differs without bound: a name shared by thousands of people
would otherwise materialize thousands of records for every staged row that mentions it.

This is the M17-containing path's behavior. A staging request built only from the four
released candidate types keeps the matcher it shipped with.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.repository import PersonNameMatch, PersonReader


class MatchDisposition(StrEnum):
    """What identity matching concluded about one staged person candidate."""

    UNMATCHED = "unmatched"
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class IdentityMatch:
    """One candidate's identity state: a disposition, and an id only when it is authoritative."""

    disposition: MatchDisposition
    person_id: str | None = None
    match_count: int = 0


def normalized_identity_tokens(tokens: list[str]) -> list[str]:
    """Return the normalized, non-blank form of one candidate's identity tokens.

    Shared by matching, the review projection, and an amendment's match-choice validation, so all
    three ask the reader the same question about the same candidate.
    """
    return [normalized for token in tokens if (normalized := normalize_name(token))]


def match_person_candidate(people: PersonReader, tokens: list[str]) -> IdentityMatch:
    """Classify one candidate against the active people its identity tokens resolve to.

    `match_count` is deliberately a count rather than the colliding ids: it is enough for a
    reviewer to see that a decision is owed, it stays bounded however many people collide, and
    it keeps staged review state from becoming a second place identity lives.
    """
    normalized = normalized_identity_tokens(tokens)
    if not normalized:
        return IdentityMatch(MatchDisposition.UNMATCHED)
    # One read, not a count followed by a lookup: staging matches before it takes the write lock,
    # so two reads could straddle a concurrent insert and report a fresh ambiguity as a confident
    # unique match — committing the candidate and its dependents against one of several people.
    matches = people.match_normalized_names(normalized)
    if matches.total == 0 or matches.first is None:
        return IdentityMatch(MatchDisposition.UNMATCHED)
    if matches.total == 1:
        return IdentityMatch(MatchDisposition.MATCHED, person_id=matches.first.id, match_count=1)
    return IdentityMatch(MatchDisposition.AMBIGUOUS, match_count=matches.total)


def person_candidate_matches(people: PersonReader, tokens: list[str], person_id: str) -> bool:
    """Whether one named person is in the set this candidate's tokens resolve to.

    This validates a reviewer's explicit `matched_person_id` choice. It asks about the whole set
    rather than the page review displayed, so a person beyond the display cap is still choosable,
    and it never reads the set itself.
    """
    normalized = normalized_identity_tokens(tokens)
    if not normalized:
        return False
    return people.matches_normalized_names(person_id, normalized)


def collision_page(people: PersonReader, tokens: list[str], limit: int) -> list[PersonNameMatch]:
    """Return the first `limit` active people these tokens resolve to, by canonical name then id."""
    normalized = normalized_identity_tokens(tokens)
    if not normalized:
        return []
    return people.page_by_normalized_names(normalized, limit)


def candidate_identity_tokens(name: str, aliases: list[dict[str, object]]) -> list[str]:
    """Return the identity tokens of one staged person candidate: its handles, then its name.

    Staging and commit both call this so that a later re-evaluation asks exactly the question
    staging asked. If the two ever drifted, an ambiguity could resolve against a different set
    of tokens than the one that raised it.
    """
    handles = [
        str(alias["value"]) for alias in aliases if alias.get("kind") == AliasKind.HANDLE.value and "value" in alias
    ]
    return [*handles, name]
