"""What the store now holds about one person, and where it may say the same thing twice.

The M19.1 timeline answers "what happened around this person, and when?". This read answers the
maintenance question: which of the durable records already stored duplicate one another, disagree
with one another, or have plainly been overtaken — and what evidence and provenance would justify
proposing to do something about it.

It proposes nothing and writes nothing. Every maintenance action M19 supports —
`correct_record`, `supersede_fact`, `merge_people` — stays an explicit, user-approved mutation. This
read only lays the evidence out.

Four rules decide everything below.

**Each record type carries its own bounded page.** Facts, traits, and observations answer different
questions, so they are not made to share a budget: a person with four hundred imported observations
must not be reported as having no facts worth looking at. Each reader returns one row past its limit
so truncation is reported without counting a table.

**Signals are deterministic, and they are relations rather than verdicts.** A signal names two
records that share a normalized predicate or category and says how they stand to each other:
duplicated, restated, contradicting, or succeeding. It never decides which one is right, never scores
a trait by counting the rows that support it, and never merges anything. That judgement needs a
reader and belongs to the user.

**Comparison is by the project's own name normalization**, the same NFKC/casefold/mark-stripping
`normalize_name` used for identity matching. "Head of Design" and "head of  design" are the same
value; nothing here reaches for a model, an embedding, or a similarity threshold to guess at
anything looser. An agent that wants a semantic reading has the bounded text in front of it and can
do that reading itself, which is exactly where M19 wants that judgement to live.

**Signals are computed only over the records the caller can actually see.** The readers filter by
disclosure level in SQL, so a withheld record cannot silently change a signal the caller is shown
while remaining invisible in the page that explains it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from itertools import islice
from typing import Final

from pydantic import BaseModel, Field

from people_context.app.insights.timeline import (
    ALL_SENSITIVITIES,
    MAX_TIMELINE_EVIDENCE_LINKS,
    ORDINARY_SENSITIVITIES,
    TimelineEvidenceLink,
)
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod, normalize_name
from people_context.ports.consolidation import (
    ConsolidationFactRow,
    ConsolidationObservationRow,
    ConsolidationTraitRow,
    PersonConsolidationReader,
)
from people_context.ports.repository import PersonReader
from people_context.ports.timeline import ENTRY_OBSERVATION

#: Rows of each record type one page carries when the caller says nothing.
DEFAULT_CONSOLIDATION_LIMIT: Final = 50

#: The narrowest and widest page a caller may ask for, per record type. Wider history is read by
#: asking for a wider page, never by an unbounded scan.
MIN_CONSOLIDATION_LIMIT: Final = 1
MAX_CONSOLIDATION_LIMIT: Final = 200

#: Relations one response reports. Every pair inside a group yields exactly one signal, so a full
#: page of 200 same-predicate facts would otherwise describe 19,900 of them; the ceiling bounds the
#: response and the work that builds it, and `signals_truncated` says when it was reached.
MAX_CONSOLIDATION_SIGNALS: Final = 200

#: Evidence citations one trait reports, the same ceiling M18.3 places on a staged trait candidate.
MAX_CONSOLIDATION_EVIDENCE_LINKS: Final = MAX_TIMELINE_EVIDENCE_LINKS

#: Two facts asserting the same normalized value over days they both cover. One of them is very
#: likely redundant, but which one is a judgement about provenance rather than a rule.
SIGNAL_DUPLICATE_FACT: Final = "duplicate_fact"

#: Two facts asserting the same normalized value over days that do not meet. The same thing was
#: recorded twice for two separate stretches; that may be a gap worth closing or two real spells.
SIGNAL_RESTATED_FACT: Final = "restated_fact"

#: Two facts asserting different normalized values over days they both cover. The store contradicts
#: itself: one may be erroneous (`correct_record`), or a transition may have been recorded as a
#: second open assertion instead of a supersession (`supersede_fact`).
SIGNAL_CONTRADICTORY_FACT: Final = "contradictory_fact"

#: Two facts asserting different normalized values over days that do not meet — a value that changed
#: and was recorded as changing. This is what a well-formed supersession leaves behind, so it is
#: reported as history rather than as a problem.
SIGNAL_SUCCEEDING_FACT: Final = "succeeding_fact"

#: Two traits in one category asserting the same normalized value: one durable characteristic
#: written down twice.
SIGNAL_DUPLICATE_TRAIT: Final = "duplicate_trait"

#: Two traits in one category asserting different normalized values. They may refine each other, or
#: one may have superseded the other — evidence and confidence decide, and a reader decides those.
SIGNAL_DIVERGENT_TRAIT: Final = "divergent_trait"

CONSOLIDATION_SIGNAL_KINDS: Final[tuple[str, ...]] = (
    SIGNAL_CONTRADICTORY_FACT,
    SIGNAL_DIVERGENT_TRAIT,
    SIGNAL_DUPLICATE_FACT,
    SIGNAL_DUPLICATE_TRAIT,
    SIGNAL_RESTATED_FACT,
    SIGNAL_SUCCEEDING_FACT,
)

#: Record types a signal relates.
SIGNAL_SUBJECT_FACT: Final = "fact"
SIGNAL_SUBJECT_TRAIT: Final = "trait"


class ConsolidationContextError(ValueError):
    """Raised when a consolidation parameter falls outside its documented range."""


class ConsolidationFact(BaseModel):
    """One durable fact, with everything a maintenance proposal needs to argue about it.

    `provenance` is the fact's own stored provenance — who or what asserted it — and is what lets a
    proposal say *why* one of two competing assertions should be believed. `source_session_id` is a
    different thing and no substitute: it names an M18 import receipt when one exists, and is null
    for anything recorded directly.
    """

    fact_id: str
    predicate: str
    value: str
    valid_from: date | None = None
    valid_to: date | None = None
    recorded_at: datetime
    confidence: float
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None


class ConsolidationTrait(BaseModel):
    """One durable trait, its recorded derivation note, and the records it cites.

    `evidence` is filtered by the *cited record's* own disclosure level, exactly as the timeline
    filters it, so a visible trait never discloses that a restricted observation exists.
    """

    trait_id: str
    category: str
    value: str
    evidence_note: str | None = None
    confidence: float
    updated_at: datetime
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None
    evidence: list[TimelineEvidenceLink] = Field(default_factory=list)
    evidence_truncated: bool = False


class ConsolidationObservation(BaseModel):
    """One durable observation, and which traits on this page rest on it.

    `cited_by_trait_ids` is the reverse of the traits' own evidence links, restricted to the traits
    actually on this page. It is what lets a reader tell three observations that independently
    support one trait from three copies of one event — the distinction M19 refuses to collapse
    automatically, and the reason no confidence here is computed from a count.
    """

    observation_id: str
    text: str
    observed_at: datetime
    sensitivity: Sensitivity
    provenance: Provenance
    source_session_id: str | None = None
    cited_by_trait_ids: list[str] = Field(default_factory=list)


class ConsolidationSignal(BaseModel):
    """One deterministic relation between two records that share a predicate or a category.

    `entity_ids` always holds exactly two ids in ascending order, so one pair is reported once and
    the same store always describes it the same way. `key` is the normalized predicate or category
    the two share — the grouping that made them comparable at all.
    """

    kind: str
    entity_type: str
    key: str
    entity_ids: list[str]


class ConsolidationContextResult(BaseModel):
    """One bounded, person-scoped view of what may need consolidating.

    `found` is false for an unknown or soft-deleted person, exactly as person context and the
    timeline report it, and carries no rows rather than an error.
    """

    found: bool
    person_id: str
    limit: int
    include_sensitive: bool = False
    facts: list[ConsolidationFact] = Field(default_factory=list)
    traits: list[ConsolidationTrait] = Field(default_factory=list)
    observations: list[ConsolidationObservation] = Field(default_factory=list)
    signals: list[ConsolidationSignal] = Field(default_factory=list)
    facts_truncated: bool = False
    traits_truncated: bool = False
    observations_truncated: bool = False
    signals_truncated: bool = False


class GetConsolidationContext:
    """Assemble one bounded, deterministic view of a person's consolidation evidence."""

    def __init__(self, people: PersonReader, consolidation: PersonConsolidationReader) -> None:
        self._people = people
        self._consolidation = consolidation

    def execute(
        self,
        person_id: str,
        *,
        limit: int = DEFAULT_CONSOLIDATION_LIMIT,
        include_sensitive: bool = False,
    ) -> ConsolidationContextResult:
        """Return the newest `limit` facts, traits, and observations plus their relations."""
        page_limit = _checked_limit(limit)
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return ConsolidationContextResult(
                found=False,
                person_id=person_id,
                limit=page_limit,
                include_sensitive=include_sensitive,
            )

        sensitivities = ALL_SENSITIVITIES if include_sensitive else ORDINARY_SENSITIVITIES
        fact_rows = self._consolidation.list_consolidation_facts(
            person_id, limit=page_limit, sensitivities=sensitivities
        )
        trait_rows = self._consolidation.list_consolidation_traits(
            person_id, limit=page_limit, sensitivities=sensitivities
        )
        observation_rows = self._consolidation.list_consolidation_observations(
            person_id, limit=page_limit, sensitivities=sensitivities
        )

        facts = [_fact(row) for row in fact_rows[:page_limit]]
        traits = [self._trait(row, sensitivities) for row in trait_rows[:page_limit]]
        observations = _observations(observation_rows[:page_limit], traits)
        signals, signals_truncated = _signals(facts, traits)
        return ConsolidationContextResult(
            found=True,
            person_id=person_id,
            limit=page_limit,
            include_sensitive=include_sensitive,
            facts=facts,
            traits=traits,
            observations=observations,
            signals=signals,
            facts_truncated=len(fact_rows) > len(facts),
            traits_truncated=len(trait_rows) > len(traits),
            observations_truncated=len(observation_rows) > len(observations),
            signals_truncated=signals_truncated,
        )

    def _trait(self, row: ConsolidationTraitRow, sensitivities: tuple[Sensitivity, ...]) -> ConsolidationTrait:
        """Build one trait entry, reading its citations under the timeline's disclosure rule.

        The lookup is per trait rather than one query over the page for the same reason M19.1 gives:
        a shared budget would let one trait with an unusual number of links consume it and leave the
        others on the page looking as though they rested on nothing.
        """
        links = self._consolidation.list_trait_evidence(
            row.trait_id,
            limit=MAX_CONSOLIDATION_EVIDENCE_LINKS,
            sensitivities=sensitivities,
        )
        return ConsolidationTrait(
            trait_id=row.trait_id,
            category=row.category,
            value=row.value,
            evidence_note=row.evidence_note,
            confidence=row.confidence,
            updated_at=row.updated_at,
            sensitivity=row.sensitivity,
            provenance=row.provenance,
            source_session_id=row.source_session_id,
            evidence=[
                TimelineEvidenceLink(evidence_type=link.evidence_type, evidence_id=link.evidence_id)
                for link in links[:MAX_CONSOLIDATION_EVIDENCE_LINKS]
            ],
            evidence_truncated=len(links) > MAX_CONSOLIDATION_EVIDENCE_LINKS,
        )


def _fact(row: ConsolidationFactRow) -> ConsolidationFact:
    return ConsolidationFact(
        fact_id=row.fact_id,
        predicate=row.predicate,
        value=row.value,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        recorded_at=row.recorded_at,
        confidence=row.confidence,
        sensitivity=row.sensitivity,
        provenance=row.provenance,
        source_session_id=row.source_session_id,
    )


def _observations(
    rows: list[ConsolidationObservationRow],
    traits: list[ConsolidationTrait],
) -> list[ConsolidationObservation]:
    """Project observations, annotating each with the traits on this page that cite it.

    Only traits on the page contribute, because only their citations were read. An observation cited
    by a trait that fell below the page bound therefore reports no citation here rather than a
    partial one — the same honesty the truncation flags carry.
    """
    citations: dict[str, list[str]] = {}
    for trait in traits:
        for link in trait.evidence:
            if link.evidence_type == ENTRY_OBSERVATION:
                citations.setdefault(link.evidence_id, []).append(trait.trait_id)
    return [
        ConsolidationObservation(
            observation_id=row.observation_id,
            text=row.text,
            observed_at=row.observed_at,
            sensitivity=row.sensitivity,
            provenance=row.provenance,
            source_session_id=row.source_session_id,
            cited_by_trait_ids=sorted(citations.get(row.observation_id, [])),
        )
        for row in rows
    ]


def _signals(
    facts: list[ConsolidationFact],
    traits: list[ConsolidationTrait],
) -> tuple[list[ConsolidationSignal], bool]:
    """Return the bounded relation page, and whether more relations exist below it.

    The stream is walked lazily and one signal past the ceiling decides truncation, so a page of
    same-predicate facts costs the ceiling rather than the square of the page.
    """
    stream = _signal_stream(facts, traits)
    page = list(islice(stream, MAX_CONSOLIDATION_SIGNALS))
    return page, next(stream, None) is not None


def _signal_stream(
    facts: list[ConsolidationFact],
    traits: list[ConsolidationTrait],
) -> Iterator[ConsolidationSignal]:
    """Relate every pair of records that shares a normalized predicate or category.

    The walk *is* the final order — facts before traits, groups by normalized key, pairs by
    ascending id — so the ceiling can cut the sequence where it stands and still describe the same
    prefix on every run. Sorting after an arbitrary truncation would have made *which* signals
    survive depend on how many there were.

    Every pair inside a group yields exactly one signal, because sharing the key is what put the two
    records in the group.
    """
    for key, fact_group in sorted(_fact_groups(facts).items(), key=lambda item: item[0]):
        for index, left_fact in enumerate(fact_group):
            for right_fact in fact_group[index + 1 :]:
                yield ConsolidationSignal(
                    kind=_fact_relation(left_fact, right_fact),
                    entity_type=SIGNAL_SUBJECT_FACT,
                    key=key,
                    entity_ids=[left_fact.fact_id, right_fact.fact_id],
                )
    for key, trait_group in sorted(_trait_groups(traits).items(), key=lambda item: item[0]):
        for index, left_trait in enumerate(trait_group):
            for right_trait in trait_group[index + 1 :]:
                yield ConsolidationSignal(
                    kind=_trait_relation(left_trait, right_trait),
                    entity_type=SIGNAL_SUBJECT_TRAIT,
                    key=key,
                    entity_ids=[left_trait.trait_id, right_trait.trait_id],
                )


def _fact_groups(facts: list[ConsolidationFact]) -> dict[str, list[ConsolidationFact]]:
    """Group facts by normalized predicate, ordering each group by id.

    Ordering members by id rather than by page position is what makes a pair's two ids ascending and
    a pair reported once, whatever order the page arrived in.
    """
    groups: dict[str, list[ConsolidationFact]] = {}
    for fact in sorted(facts, key=lambda fact: fact.fact_id):
        groups.setdefault(normalize_name(fact.predicate), []).append(fact)
    return groups


def _trait_groups(traits: list[ConsolidationTrait]) -> dict[str, list[ConsolidationTrait]]:
    """Group traits by normalized category, ordering each group by id."""
    groups: dict[str, list[ConsolidationTrait]] = {}
    for trait in sorted(traits, key=lambda trait: trait.trait_id):
        groups.setdefault(normalize_name(trait.category), []).append(trait)
    return groups


def _fact_relation(left: ConsolidationFact, right: ConsolidationFact) -> str:
    """Name how two facts asserting one predicate stand to each other."""
    same_value = normalize_name(left.value) == normalize_name(right.value)
    overlapping = _period(left).overlaps(_period(right))
    if same_value:
        return SIGNAL_DUPLICATE_FACT if overlapping else SIGNAL_RESTATED_FACT
    return SIGNAL_CONTRADICTORY_FACT if overlapping else SIGNAL_SUCCEEDING_FACT


def _trait_relation(left: ConsolidationTrait, right: ConsolidationTrait) -> str:
    """Name how two traits in one category stand to each other."""
    same_value = normalize_name(left.value) == normalize_name(right.value)
    return SIGNAL_DUPLICATE_TRAIT if same_value else SIGNAL_DIVERGENT_TRAIT


def _period(fact: ConsolidationFact) -> ValidityPeriod:
    """Return the fact's stored validity, using the domain's own inclusive overlap semantics.

    Reusing `ValidityPeriod.overlaps` rather than re-deriving the comparison is deliberate: M15's
    doctor already decides fact conflicts with it, so a contradiction reported here and a duplicate
    reported there cannot disagree about what "at the same time" means.
    """
    return ValidityPeriod(valid_from=fact.valid_from, valid_to=fact.valid_to)


def _checked_limit(limit: int) -> int:
    if limit < MIN_CONSOLIDATION_LIMIT or limit > MAX_CONSOLIDATION_LIMIT:
        raise ConsolidationContextError(
            f"limit must be between {MIN_CONSOLIDATION_LIMIT} and {MAX_CONSOLIDATION_LIMIT}"
        )
    return limit
