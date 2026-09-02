"""Accept a person by id or by name on read-only tools.

Every read took a ``person_id`` and so cost an agent a ``resolve_person`` round-trip first. A read may
instead take ``person`` — the name as the user said it — and resolve it here with the same use case
and the same contract: an ambiguous name returns the candidates rather than a guess, and nothing is
read for a person the resolver was not confident about. Ids stay the precise, preferred handle.

Confidence is judged by *how* the name matched, not by its score. An exact name or alias, and a
lexical hit, both mean the store really holds the name that was typed — "Amina" genuinely matching
"Amina Hassan" is the ordinary way people refer to each other, and scores around 0.65. A ``fuzzy``
candidate means the opposite: the resolver found nothing matching the string and fell back to
bounded edit distance, so "Danial Okafor" surfaces "Daniel Okafor" at 0.45. Reading that person's
context, timeline, graph, guidance, or reminders would answer a question about one person with
another person's records, which is why a single fuzzy candidate is returned for confirmation
instead of being followed. The write path in ``app/capture`` is stricter still — it requires an
exact match or a strong score before recording anything — because a misattributed write persists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from people_context.app.people import FUZZY_MATCH_REASON

if TYPE_CHECKING:
    from people_context.adapters.runtime import RuntimeUseCases


def resolve_reference(deps: RuntimeUseCases, *, person_id: str | None, person: str | None) -> str | dict[str, Any]:
    """Return the person id to read, or the structured payload to return instead."""
    if person_id:
        return person_id
    if not person:
        return {"error": "missing_person", "message": "Pass `person_id` (from resolve_person) or `person` (a name)."}
    resolution = deps.resolve_person.execute(person, limit=5)
    if not resolution.candidates:
        return {"error": "person_not_found", "message": f"no one matching {person!r} is stored", "person": person}
    if resolution.ambiguous:
        return {
            "error": "ambiguous_person",
            "message": f"{person!r} matches several people; ask which one or pass a person_id",
            "candidates": [candidate.model_dump(mode="json") for candidate in resolution.candidates],
        }
    top = resolution.candidates[0]
    if top.match_reason == FUZZY_MATCH_REASON:
        return {
            "error": "unconfirmed_person",
            "message": (
                f"no one is stored under {person!r}; {top.canonical_name!r} is only a near-spelling of it. "
                "Confirm with the user before reading their records, then pass that name or a person_id."
            ),
            "candidates": [candidate.model_dump(mode="json") for candidate in resolution.candidates],
        }
    return top.person_id
