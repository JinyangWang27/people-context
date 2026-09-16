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

Two bounds *are* re-checked, and the difference is whether refusing could reject this installation's
own data. A trait's evidence budget and `stated_by` are both unconditional at the input boundary, so
nothing staged here can exceed them and holding a restored row to the same number turns away only a
document that was hand-edited or corrupted. Both are also bounds whose breach would be carried
forward: an over-budget trait makes one trait's retrieval unbounded, and an oversized attribution is
committed into durable provenance and then read back through every surface that reports it. The
older unbounded strings beside them — an observation's `text`, a fact's `value` — keep their released
shape, because narrowing those *would* refuse rows this installation legitimately stored.

`extra="forbid"` is the other half of the contract: staging is where extraction output stops being
prose, so a key nothing here declares is unexplained text that review would display and every later
bundle would carry.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

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

from people_context.domain.group import (
    GroupKind,
    GroupName,
    MembershipRole,
    TemporalBasis,
    check_temporal_basis,
)
from people_context.domain.person import AliasKind
from people_context.domain.shared import Confidence, Sensitivity, StatedByText, ValidityPeriod
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

#: A durable id a persisted candidate names, with no ceiling of its own.
#:
#: The evidence ceiling above is the caller-supplied label's, shared by the durable id beside it.
#: A group reference has no label half, so there is nothing free-form to bound — only an id that
#: matches a stored row or does not. The bundle's `Identifier` accepts any non-blank string, and
#: a restored group may carry one longer than any staging label would be; refusing it here would
#: make that group unusable through the very workflow `find_groups` points a caller at.
DurableIdentifier = Annotated[str, AfterValidator(_non_blank_token)]

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
    stated_by: StatedByText | None = None


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
    stated_by: StatedByText | None = None


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


class StagedGroup(StrictStagedModel):
    """A persisted group candidate: the input fields minus the batch-local `ref`.

    `group_id` is the caller's explicit decision to record against a group that already exists.
    It is kept here rather than resolved at staging time because resolving it would mean a read
    whose answer could change before commit, and because a group deleted between the two is a
    candidate commit must decline — not one it silently redirects.

    Both ids use the opaque identifier type rather than a stripped string, for the reason spelled
    out on `EvidenceIdentifier`: an id is matched exactly against a durable row, and a restored
    one may carry whatever the bundle's `Identifier` contract allowed — including its length,
    which is why `DurableIdentifier` imposes no ceiling where the evidence type does.
    """

    type: Literal["group"]
    name: GroupName
    kind: GroupKind
    organization_id: DurableIdentifier | None = None
    group_id: DurableIdentifier | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    stated_by: StatedByText | None = None


class StagedMembership(StrictStagedModel):
    """A persisted membership candidate, its person and group already rewritten to candidate ids.

    The discriminator is `group_membership` rather than `membership` because a candidate's type
    and the entity type of the commit mapping it produces are checked against each other: a
    restore refuses a mapping that "claims a group_membership for a membership candidate". Every
    other type already spells the two the same way, and naming this one for the record it becomes
    keeps that one-to-one rule instead of adding a correspondence table beside it.

    `temporal_basis` is resolved at staging rather than left absent, so the row states plainly
    what it asserts to anyone running `import review` — and so a restore cannot put back a
    membership whose basis and dates disagree, which would fail at its durable write after
    earlier candidates in the same commit had already written.

    The bounds are held to the same order the durable `ValidityPeriod` requires, and for the same
    reason rather than a different one: the staging boundary refuses a reversed range, so nothing
    this installation stored can carry one, and holding a restored row to it turns away only a
    document that was hand-edited or corrupted — one whose batch would otherwise restore, list
    for review, and then raise from inside the commit transaction.
    """

    type: Literal["group_membership"]
    person_candidate_id: NonBlank
    group_candidate_id: NonBlank
    role: MembershipRole = MembershipRole.MEMBER
    valid_from: date | None = None
    valid_to: date | None = None
    temporal_basis: TemporalBasis
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    stated_by: StatedByText | None = None

    @model_validator(mode="after")
    def _check_basis(self) -> StagedMembership:
        # Constructing the durable type is the check: one rule, in the place that owns it.
        ValidityPeriod(valid_from=self.valid_from, valid_to=self.valid_to)
        check_temporal_basis(self.temporal_basis, self.valid_from, self.valid_to)
        return self


StagedCandidate = Annotated[
    StagedPerson
    | StagedInteraction
    | StagedAffiliation
    | StagedFact
    | StagedObservation
    | StagedTrait
    | StagedRelationship
    | StagedGroup
    | StagedMembership,
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
    "group": StagedGroup,
    "group_membership": StagedMembership,
}

_STAGED_ADAPTER: TypeAdapter[Any] = TypeAdapter(StagedCandidate)


def parse_staged_candidate(candidate: dict[str, Any]) -> Any:
    """Return the parsed persisted candidate, or raise ``ValidationError``."""
    return _STAGED_ADAPTER.validate_python(candidate)


#: Fields M18.3 added to the persisted trait candidate.
#:
#: A bundle version that predates them must still reject them, because a released version is a
#: closed shape: a reader that accepts a field must understand it, and the reader that wrote a
#: version-2 document had no evidence relation to resolve these against.
EVIDENCE_STAGED_FIELDS: Final[tuple[str, ...]] = ("evidence_candidate_ids", "evidence_ids")

#: The field M22.1 added to the persisted fact and affiliation candidates.
#:
#: Gated for the same reason as the evidence fields above: a bundle version that predates the
#: field must still reject it. Assertion attribution is not decoration a reader may ignore — a
#: version-3 reader would restore the candidate and then commit it, silently dropping the very
#: attribution that keeps a source's claim from being read as verified fact.
ATTRIBUTION_STAGED_FIELDS: Final[tuple[str, ...]] = ("stated_by",)

#: The candidate types M28.3 added.
#:
#: Gated like the fields above, and for a stronger reason: a whole type is not something a
#: reader fails closed on by forbidding extras, because the discriminator picks the model before
#: any field is seen. A bundle version that predates these types must refuse them outright, or a
#: reader written against that version would restore a membership whose group reference it has
#: no way to resolve and then report the batch as committable.
GROUP_STAGED_TYPES: Final[tuple[str, ...]] = ("group", "group_membership")


def staged_candidate_error(
    candidate: dict[str, Any],
    *,
    evidence_allowed: bool = True,
    attribution_allowed: bool = True,
    group_types_allowed: bool = True,
) -> str | None:
    """Return why a persisted candidate is unacceptable, naming no value it carries.

    Pydantic's own message quotes rejected input, and a staged candidate is the one place a
    caller's raw source text would sit. The report is therefore built from the location and the
    error type only — enough to find the offending field, never enough to leak what was in it.

    ``evidence_allowed`` and ``attribution_allowed`` are how an older bundle version keeps its
    released shape. The models here describe what this installation persists *today*; validating a
    version-2 document through them unchanged would accept an M18.3 field under a declaration that
    predates it, and a version-3 document an M22.1 one, which is exactly the silent upgrade the
    per-version contract exists to prevent. The flags are independent because the versions are:
    version 2 predates both fields, version 3 only the attribution.

    ``group_types_allowed`` is the same idea one level up, for the candidate types M28.3 added.
    It is checked before the models are consulted at all, because the discriminated union would
    otherwise accept a type no version through 5 had any way to commit.
    """
    if not group_types_allowed and candidate.get("type") in GROUP_STAGED_TYPES:
        return "type (literal_error)"
    forbidden: tuple[str, ...] = ()
    if not evidence_allowed:
        forbidden += EVIDENCE_STAGED_FIELDS
    if not attribution_allowed:
        forbidden += ATTRIBUTION_STAGED_FIELDS
    present = sorted(field for field in forbidden if field in candidate)
    if present:
        return "; ".join(f"{field} (extra_forbidden)" for field in present)
    try:
        parse_staged_candidate(candidate)
    except ValidationError as exc:
        return "; ".join(sorted({_location(error) for error in exc.errors()}))
    return None


def _location(error: Any) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "candidate"
    return f"{location} ({error.get('type', 'invalid')})"
