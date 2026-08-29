"""The durable link from an inferred trait to the records it rests on.

A trait is the one record type People Context stores that nobody asserted directly: it is a
generalization somebody — or some agent — drew out of material. M18.3 lets that generalization
name the durable observations and interactions it was drawn from, so "reads as blunt in writing"
can be traced back to the three messages that produced it rather than resting on a note.

The rules the relation is worth having *because of* live here, where both the staging boundary
and the restore validator can read them:

- only observations and interactions may be cited. A trait citing another trait would be a
  belief chain, and each link in it would look like grounding while adding no observed material;
- evidence must belong to the trait's own subject. Without that rule, Alice's trait becomes a
  way to read the metadata of an observation about Bob;
- a link is deterministic and id-based. Nothing here re-derives a link from text.

The character ceiling is a *process-boundary* bound on what a caller may submit, not a claim
about what an identifier looks like. Ids are format-opaque: a restored `obs-1` is exactly as
addressable as a generated ULID, and nothing here case-folds or normalizes one.
"""

from __future__ import annotations

from typing import Final

#: Record types a trait may cite as evidence, in the order a bare id is resolved against them.
#:
#: Ids generated here are ULIDs and never collide, but a restored or hand-authored id need not
#: be one. A fixed order makes the resolution of such an id deterministic rather than dependent
#: on table iteration.
TRAIT_EVIDENCE_TYPES: Final[tuple[str, ...]] = ("observation", "interaction")

#: Characters a caller-supplied evidence reference or durable evidence id may carry.
MAX_EVIDENCE_REFERENCE_CHARS: Final = 256

#: Combined `evidence_refs` + `evidence_ids` one staged trait candidate may carry.
MAX_TRAIT_EVIDENCE_LINKS: Final = 32


def trait_evidence_key(trait_id: str, evidence_type: str, evidence_id: str) -> str:
    """Return the accountability identity of one link, naming all three of its key components.

    A link is identified by its whole primary key, and the type is not decoration there: ids are
    opaque and nothing makes them unique *across* tables, so a restored store may legitimately
    hold an observation and an interaction sharing one id, and one trait may cite both. Those are
    two distinct rows. An identity that dropped the type would give them the same name, and a
    forget of one would emit a tombstone that could not say which row to erase.

    Both the write that journals a link and the forget that redacts it read this one function,
    because the two must agree exactly: redaction finds that history by the very string the write
    recorded it under, and a drift between them would silently leave it behind.
    """
    return f"{trait_id}:{evidence_type}:{evidence_id}"
