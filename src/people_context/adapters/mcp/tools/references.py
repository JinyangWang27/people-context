"""Accept a person by id or by name on read-only tools.

Every read took a ``person_id`` and so cost an agent a ``resolve_person`` round-trip first. A read may
instead take ``person`` — the name as the user said it — and resolve it here with the same use case
and the same contract: an ambiguous name returns the candidates rather than a guess, and nothing is
read for a person the resolver was not confident about. Ids stay the precise, preferred handle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    return resolution.candidates[0].person_id
