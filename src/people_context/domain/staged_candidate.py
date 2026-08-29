"""The strict shape of a candidate as it is *persisted*, which is not the shape it arrives in.

A caller hands the stager batch-local references — `person_ref`, `from_ref`, `participant_refs` —
and the stager rewrites them into canonical candidate ids before the row is written. What lands in
`import_staging` is therefore a third thing: the input model's own fields, minus the refs, plus the
rewritten ids, plus a person row's match outcome. Commit reads that shape and a restore has to
accept exactly it.

These models are that shape, declared where both the bundle validator and the erasure logic can
reach them. They exist because two failure modes have no good report at commit time:

- a field commit indexes is missing, so it raises mid-transaction — after earlier candidates in
  the same commit have already written durable records;
- a value is structurally wrong in a way only the durable write would catch (a raw string where an
  alias object belongs, an unparseable date, a category outside the vocabulary), which fails the
  same way and, until it does, shows whatever the value contains to anyone running `import review`.

Both are refused here instead, where the batch can still be declined whole.

What is deliberately **not** re-checked is the extraction boundary's byte budgets. Those bound what
one caller may submit in a single request; they protect request and storage size at the door, and
the durable write imposes none of them. Re-imposing them on a restore would refuse a bundle whose
rows this installation itself accepted and stored.

`extra="forbid"` is the other half of the contract: staging is where extraction output stops being
prose, so a key nothing here declares is unexplained text that review would display and every later
bundle would carry.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from people_context.domain.person import AliasKind
from people_context.domain.shared import Confidence, Sensitivity
from people_context.domain.trait import TraitCategory
from people_context.domain.trait_evidence import MAX_EVIDENCE_REFERENCE_CHARS, MAX_TRAIT_EVIDENCE_LINKS

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

def _non_blank_token(value: str) -> str:
    """Accept one opaque identity token, checking it is not blank without rewriting it.

    An evidence reference is matched exactly against a durable id, and the bundle's own
    `Identifier` contract accepts any non-blank string. Trimming one here would make a restored
    id unciteable, or resolve it to a different record whose id is the trimmed form.
    """
    if not value.strip():
        raise ValueError("an evidence identifier must not be blank")
    return value


#: A durable evidence id, or a canonical candidate id standing in for one until commit.
#:
#: The ceiling is the staging boundary's, applied here too because a persisted candidate is what a
#: restore puts back: accepting a longer one from a hand-edited bundle would reintroduce exactly
#: the unbounded field the input model refuses.
EvidenceIdentifier = Annotated[
    str,
    StringConstraints(max_length=MAX_EVIDENCE_REFERENCE_CHARS),
    AfterValidator(_non_blank_token),
]

#: What identity matching concluded about a staged person candidate.
#:
#: The producing enum lives in the application layer, which the domain does not import; the values
#: are pinned against it by `tests/app/imports/test_persisted_candidate_shape.py`.
MatchDispositionValue = Literal["unmatched", "matched", "ambiguous"]


class StrictStagedModel(BaseModel):
    """A persisted candidate accepts exactly its declared fields and nothing else."""

    model_config = ConfigDict(extra="forbid")


class StagedAlias(StrictStagedModel):
    """One alias inside a persisted person candidate.

    Commit re-validates each of these through `AliasInput`, so a bare string here is not a
    tolerable looseness: it raises there, having first been shown by review.
    """

    value: NonBlank
    kind: AliasKind = AliasKind.OTHER
    lang: str | None = None
    script: str | None = None


class StagedPerson(StrictStagedModel):
    """A persisted person candidate: the input fields minus `ref`, plus the match outcome.

    `matched_person_id` is written after the model dump rather than through it, so unlike every
    other optional it is present-and-null when nothing matched. The disposition and count travel
    only on the ambiguity-preserving path, which is why both stay optional here.
    """

    type: Literal["person"]
    name: NonBlank
    aliases: list[StagedAlias]
    summary: str | None = None
    message_id: str | None = None
    date: datetime | None = None
    matched_person_id: str | None = None
    match_disposition: MatchDispositionValue | None = None
    match_count: int | None = None


class StagedInteraction(StrictStagedModel):
    """A persisted interaction candidate, its participants already rewritten to candidate ids."""

    type: Literal["interaction"]
    summary: NonBlank
    participant_candidate_ids: list[NonBlank]
    date: datetime
    channel: str | None = None
    message_id: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL


class StagedAffiliation(StrictStagedModel):
    """A persisted affiliation candidate."""

    type: Literal["affiliation"]
    person_candidate_id: NonBlank
    org: NonBlank
    role: NonBlank
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None


class StagedFact(StrictStagedModel):
    """A persisted fact candidate."""

    type: Literal["fact"]
    person_candidate_id: NonBlank
    predicate: NonBlank
    value: NonBlank
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL


class StagedObservation(StrictStagedModel):
    """A persisted observation candidate."""

    type: Literal["observation"]
    person_candidate_id: NonBlank
    text: NonBlank
    observed_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL


class StagedTrait(StrictStagedModel):
    """A persisted trait candidate, held to the evidence the staging boundary requires.

    The two evidence collections are what a caller's `evidence_refs` and `evidence_ids` become
    after staging. `evidence_candidate_ids` names other candidates in this batch and resolves
    through their commit mappings; `evidence_ids` names durable records directly. Both default
    to empty and are written only when non-empty, so a trait staged before M18.3 — or one that
    cites nothing — keeps the persisted shape it always had.
    """

    type: Literal["trait"]
    person_candidate_id: NonBlank
    category: TraitCategory
    value: NonBlank
    evidence_note: NonBlank
    confidence: Confidence
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    evidence_candidate_ids: list[EvidenceIdentifier] = Field(default_factory=list)
    evidence_ids: list[EvidenceIdentifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_evidence(self) -> StagedTrait:
        """Hold a persisted trait to the same evidence budget the input boundary applies.

        Uniqueness is checked per collection and the budget across both, exactly as the caller's
        request was checked. A duplicate would be a link the store already refuses as a primary
        key, and an over-budget row would make one trait's retrieval unbounded.
        """
        for field_name, values in (
            ("evidence_candidate_ids", self.evidence_candidate_ids),
            ("evidence_ids", self.evidence_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not repeat an identifier")
        if len(self.evidence_candidate_ids) + len(self.evidence_ids) > MAX_TRAIT_EVIDENCE_LINKS:
            raise ValueError(f"a trait cites at most {MAX_TRAIT_EVIDENCE_LINKS} pieces of evidence")
        return self


class StagedRelationship(StrictStagedModel):
    """A persisted relationship candidate, both ends already rewritten to candidate ids.

    There is no `sensitivity`, exactly as there is none on the input model: the durable
    relationship carries no disclosure level, so accepting one here would imply a protection the
    graph cannot enforce.
    """

    type: Literal["relationship"]
    from_candidate_id: NonBlank
    to_candidate_id: NonBlank
    relationship_type: NonBlank
    confidence: Confidence | None = None


StagedCandidate = Annotated[
    StagedPerson
    | StagedInteraction
    | StagedAffiliation
    | StagedFact
    | StagedObservation
    | StagedTrait
    | StagedRelationship,
    Field(discriminator="type"),
]

STAGED_CANDIDATE_MODELS: dict[str, type[StrictStagedModel]] = {
    "person": StagedPerson,
    "interaction": StagedInteraction,
    "affiliation": StagedAffiliation,
    "fact": StagedFact,
    "observation": StagedObservation,
    "trait": StagedTrait,
    "relationship": StagedRelationship,
}

_STAGED_ADAPTER: TypeAdapter[Any] = TypeAdapter(StagedCandidate)


def parse_staged_candidate(candidate: dict[str, Any]) -> Any:
    """Return the parsed persisted candidate, or raise ``ValidationError``."""
    return _STAGED_ADAPTER.validate_python(candidate)


def staged_candidate_error(candidate: dict[str, Any]) -> str | None:
    """Return why a persisted candidate is unacceptable, naming no value it carries.

    Pydantic's own message quotes rejected input, and a staged candidate is the one place a
    caller's raw source text would sit. The report is therefore built from the location and the
    error type only — enough to find the offending field, never enough to leak what was in it.
    """
    try:
        parse_staged_candidate(candidate)
    except ValidationError as exc:
        return "; ".join(sorted({_location(error) for error in exc.errors()}))
    return None


def _location(error: Any) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "candidate"
    return f"{location} ({error.get('type', 'invalid')})"
