"""Read-side port for the bounded person consolidation context.

Consolidation asks a different question from the M19.1 timeline. The timeline asks "what happened
around this person, and when?"; this read asks "what does the store now hold about this person that
might say the same thing twice, disagree with itself, or have been overtaken?" — the evidence an
agent needs before *proposing* a correction, a supersession, or a merge to the user.

It is a read, and only a read. Nothing here writes, and the maintenance decision it feeds is
explicit and human-approved; M19 deliberately ships no autonomous belief updater.

Three shapes in this contract are worth stating explicitly.

**Rows are bounded per record type, newest first, and the bound is per type rather than shared.**
Facts, traits, and observations answer different questions, so one dense collection must not be able
to consume the page and leave the others looking empty. Each read returns one row past its limit, so
the application can report truncation without counting a table.

**Facts are ordered by the same effective instant the timeline uses**: `valid_from` when the fact
asserts one, otherwise the time it was recorded. Reading the two surfaces with the same limit
therefore describes the same recent window, rather than two windows a caller has to reconcile.

**Sensitivity travels down into the read.** Facts, traits, and observations all carry a disclosure
level, and the caller passes the levels it may disclose. Selecting a page and filtering it afterwards
would return a short page that silently withheld the fact that something was filtered out — and, for
a signal computed *over* the page, would let a withheld record change the answer without appearing
in it.

**Every row carries the record's own stored `Provenance`**, and every type carries it alike. A
maintenance proposal is an argument about which of two records to believe, and who asserted a thing
is half of that argument — so a trait entered by the operator and a trait an importer wrote down
must be distinguishable here, not only when one of them happens to have an M18 import receipt.
`source_session_id` is a different fact from provenance and does not stand in for it: it names an
import receipt when one exists and is null for everything recorded directly. Provenance discloses no
raw source material, because none is stored, and the record's own level already decides whether the
row appears at all — the same rule under which `get_person_context` already returns these records
with their provenance attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from people_context.domain.shared import Provenance, Sensitivity
from people_context.ports.timeline import TimelineEvidenceRow


@dataclass(frozen=True)
class ConsolidationFactRow:
    """One durable fact as the consolidation read sees it.

    The whole assertion travels, not a summary of it: predicate, value, and both validity endpoints
    are what decide whether two facts duplicate, contradict, or succeed one another, and
    `recorded_at` is what distinguishes the assertion's subject-matter time from the time it was
    written down.
    """

    fact_id: str
    predicate: str
    value: str
    valid_from: date | None
    valid_to: date | None
    recorded_at: datetime
    confidence: float
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None


@dataclass(frozen=True)
class ConsolidationTraitRow:
    """One durable trait, with the human derivation note it was recorded with.

    `evidence_note` is the recorder's own short derivation, which M17.1 requires of a staged trait.
    It is carried because a maintenance proposal that cannot see why a trait exists cannot argue
    about whether a second trait restates it.
    """

    trait_id: str
    category: str
    value: str
    evidence_note: str | None
    confidence: float
    updated_at: datetime
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None


@dataclass(frozen=True)
class ConsolidationObservationRow:
    """One durable observation, carried with its text.

    The text is the point. Deterministic policy can tell that two observations exist; only a reader
    can tell whether they are two independent pieces of evidence for one trait or one event written
    down twice, and M19 gives that judgement to the agent and the user rather than to a formula.
    """

    observation_id: str
    text: str
    observed_at: datetime
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None


@runtime_checkable
class PersonConsolidationReader(Protocol):
    """Read one person's maintenance evidence as bounded pages, never as tables to slice."""

    def list_consolidation_facts(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationFactRow]:
        """Return the person's newest facts, reading one row past `limit`.

        Newest is by asserted `valid_from` when the fact carries one and by `recorded_at` otherwise,
        the same placement the timeline uses, with the id breaking an exact tie.
        """
        ...

    def list_consolidation_traits(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationTraitRow]:
        """Return the person's most recently updated traits, reading one row past `limit`."""
        ...

    def list_consolidation_observations(
        self,
        person_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[ConsolidationObservationRow]:
        """Return the person's newest observations, reading one row past `limit`."""
        ...

    def list_trait_evidence(
        self,
        trait_id: str,
        *,
        limit: int,
        sensitivities: tuple[Sensitivity, ...],
    ) -> list[TimelineEvidenceRow]:
        """Return one trait's disclosable M18.3 evidence citations, reading one row past `limit`.

        The contract is exactly the timeline's, deliberately: the *cited record's* own level decides
        whether a trait may name it, so a visible trait never discloses that restricted evidence
        exists. Restating it here rather than inventing a second citation shape keeps one meaning of
        "what this trait rests on" across both M19 reads.
        """
        ...
