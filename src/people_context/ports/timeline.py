"""Read-side port for the bounded person timeline.

The timeline is a **projection** over records this database already holds, never a second event
store: nothing here is written, backfilled, or kept in step with the canonical tables. A row is
one durable record — an interaction, an observation, a fact, an affiliation, a relationship, or a
trait — described well enough to place it on a chronology and look it up by id.

Two shapes in this contract are worth stating explicitly.

**Every row carries the stored timestamp it was placed by, and says which one that was.** A record
that carries a validity period is placed by `valid_from` when it has one; otherwise it falls back
to the time it was recorded, and `basis` names that fallback. Nothing invents a timestamp, and
nothing is silently dropped for lacking one.

**Sensitivity is what the record itself carries, or nothing at all.** Facts, observations,
interactions, and traits have a disclosure level; affiliations and relationships have no such
field in the durable contract, so their level is `None` and they are ordinary by construction —
the same reading M17.1 recorded when it refused to mint a candidate-only relationship sensitivity
that commit would discard.

Disclosure filtering is application policy, but the *levels* travel down into the read: a bounded
page must be selected from the rows the caller may actually see, or a page could arrive short — or
empty — while withholding the very fact that something was filtered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol, runtime_checkable

from people_context.domain.shared import Sensitivity

#: Record types the timeline projects. These are the durable entity types M18.1 already names in
#: its commit mappings, so a timeline entry and a provenance mapping talk about the same thing.
ENTRY_INTERACTION: Final = "interaction"
ENTRY_OBSERVATION: Final = "observation"
ENTRY_FACT: Final = "fact"
ENTRY_AFFILIATION: Final = "affiliation"
ENTRY_RELATIONSHIP: Final = "relationship"
ENTRY_TRAIT: Final = "trait"

TIMELINE_ENTRY_TYPES: Final[tuple[str, ...]] = (
    ENTRY_AFFILIATION,
    ENTRY_FACT,
    ENTRY_INTERACTION,
    ENTRY_OBSERVATION,
    ENTRY_RELATIONSHIP,
    ENTRY_TRAIT,
)

#: Which stored column supplied a row's `effective_at`. `valid_from` is an effective date the
#: writer asserted; the other four are times a record was made. Naming the basis is what keeps the
#: fallback honest: a caller can tell "this happened then" from "this was written down then".
BASIS_OCCURRED_AT: Final = "occurred_at"
BASIS_OBSERVED_AT: Final = "observed_at"
BASIS_UPDATED_AT: Final = "updated_at"
BASIS_RECORDED_AT: Final = "recorded_at"
BASIS_CREATED_AT: Final = "created_at"
BASIS_VALID_FROM: Final = "valid_from"

TIMELINE_BASES: Final[tuple[str, ...]] = (
    BASIS_CREATED_AT,
    BASIS_OBSERVED_AT,
    BASIS_OCCURRED_AT,
    BASIS_RECORDED_AT,
    BASIS_UPDATED_AT,
    BASIS_VALID_FROM,
)


@dataclass(frozen=True)
class TimelineRow:
    """One durable record as the timeline projection sees it.

    `summary` and `detail` are the record's own display components — an interaction's summary, a
    fact's predicate and value, an affiliation's role and organization — never import material.
    People Context stores no raw source, so there is none to leak here.

    `source_session_id` is the M18 receipt of the import that first produced this record, when one
    did. A record entered by hand carries none, and that absence is not a defect.
    """

    entry_type: str
    entry_id: str
    effective_at: datetime
    basis: str
    summary: str
    detail: str | None = None
    sensitivity: Sensitivity | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_session_id: str | None = None


@dataclass(frozen=True)
class TimelineEvidenceRow:
    """One M18.3 evidence citation of a trait, carrying the cited record's own level.

    The level travels with the link for the reason M18.3 gave it: the disclosure decision belongs
    to the evidence, not to the trait it grounds. A `None` level means the cited record could not
    be read at all, which fails closed rather than naming an id whose record is unaccounted for.
    """

    evidence_type: str
    evidence_id: str
    sensitivity: Sensitivity | None = None


@runtime_checkable
class PersonTimelineReader(Protocol):
    """Read one person's chronology as bounded pages, never as a table to slice."""

    def list_timeline_rows(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[TimelineRow]:
        """Return the newest rows for one person, reading one row past `limit`.

        Only records at one of `sensitivities`, or carrying no level at all, participate. The
        extra row is how the caller learns another page exists without counting the table.
        """
        ...

    def list_trait_evidence(self, trait_id: str, *, limit: int) -> list[TimelineEvidenceRow]:
        """Return one trait's evidence citations in stable order, reading one row past `limit`."""
        ...
