"""Deterministic person-candidate identity matching for extraction batches.

The released staging matcher answers one question — "is there an existing person to attach
this to?" — and reports only an id or nothing. That collapses two different situations into
the same `matched_person_id=null`: nobody matched, and several people matched. The first is a
new identity; the second is a question nobody has answered yet, and committing it as a new
person is how a duplicate gets created out of an ambiguity.

Extraction batches keep the two apart. Matching takes the union of the active people every
identity token resolves to, so a unique hit on one token cannot mask a conflict on another,
and the resulting disposition — not the presence of an id — is what commit acts on.

This is the M17-containing path's behavior. A staging request built only from the four
released candidate types keeps the matcher it shipped with.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.repository import PersonReader


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


def match_person_candidate(people: PersonReader, tokens: list[str]) -> IdentityMatch:
    """Classify one candidate against the active people its identity tokens resolve to.

    `match_count` is deliberately a count rather than the colliding ids: it is enough for a
    reviewer to see that a decision is owed, it stays bounded however many people collide, and
    it keeps staged review state from becoming a second place identity lives.
    """
    matched: set[str] = set()
    for token in tokens:
        normalized = normalize_name(token)
        if not normalized:
            continue
        matched.update(person.id for person in people.find_by_normalized_name(normalized))
    if not matched:
        return IdentityMatch(MatchDisposition.UNMATCHED)
    if len(matched) == 1:
        return IdentityMatch(MatchDisposition.MATCHED, person_id=next(iter(matched)), match_count=1)
    return IdentityMatch(MatchDisposition.AMBIGUOUS, match_count=len(matched))


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
