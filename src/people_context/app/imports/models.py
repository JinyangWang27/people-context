"""Validated import candidates and stable workflow results."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from people_context.domain.group import (
    GroupKind,
    GroupName,
    MembershipRole,
    TemporalBasis,
    check_temporal_basis,
    resolve_temporal_basis,
)
from people_context.domain.person import AliasKind
from people_context.domain.relationship_vocabulary import normalize_relationship_type
from people_context.domain.shared import Confidence, Sensitivity, StatedByText, ValidityPeriod
from people_context.domain.trait import TraitCategory
from people_context.domain.trait_evidence import MAX_EVIDENCE_REFERENCE_CHARS, MAX_TRAIT_EVIDENCE_LINKS


class ImportPipelineError(Exception):
    """Raised for staging-batch and accepted-candidate validation failures."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class ImportBatchResult(BaseModel):
    """Summary of one atomically staged extraction batch.

    ``source_session_id`` names the durable receipt this batch belongs to, and is absent for a
    batch that is not source-tracked — an inline-content import, or one staged before M18.

    ``duplicate`` says the canonical claim for this source was already owned, so nothing new was
    staged and every field above describes the batch that already exists. It is not an error:
    re-reading the same export is the ordinary way someone discovers they already imported it.

    ``reviewable`` says the batch still has staged rows to review and commit. A fully committed
    batch may have had them cleaned up, or may have arrived from a bundle that carries only its
    durable outcomes, and pointing someone at review for one of those would name a batch review
    can no longer find. It is ``True`` for every freshly staged batch.
    """

    batch_id: str
    candidate_count: int
    skipped_message_ids: list[str] = Field(default_factory=list)
    skipped_without_id: int = 0
    skipped_cards: list[dict[str, int | str]] = Field(default_factory=list)
    source_session_id: str | None = None
    duplicate: bool = False
    reviewable: bool = True


#: Colliding people one ambiguous person row may list, and the characters each name may carry.
#:
#: The list is capped because a common name can collide with any number of people and a review
#: read must stay bounded by the staged payload it already measures. Each name is cut because
#: stored canonical names have no length bound of their own. Neither cap limits *choice*: a patch
#: naming any person the matcher found is accepted, and a reviewer finds one past the cap through
#: the bounded `resolve_person` and `search_people` reads.
MAX_MATCH_CANDIDATES: Final = 10
MAX_MATCH_CANDIDATE_NAME_CHARS: Final = 256


class MatchCandidate(BaseModel):
    """One existing person an ambiguous person candidate could be.

    Computed at read time and never stored: the stored row keeps a count, because staged review
    state must not become a second place identity lives.
    """

    id: str
    canonical_name: str
    name_truncated: bool = False


class ImportReviewRow(BaseModel):
    """Review-safe staging row.

    `match_candidates` is present only for an ambiguous person row — the one case where a reviewer
    is owed a decision they cannot express through the candidate's own name and handles. It is
    absent, rather than empty, everywhere else, so "no decision is owed here" and "the decision has
    no options" stay distinguishable.
    """

    id: str
    source: str
    status: str
    candidate: dict[str, Any]
    match_candidates: list[MatchCandidate] | None = None
    match_candidates_truncated: bool = False


class ImportReviewResult(BaseModel):
    """All candidates and statuses for one batch.

    `batch_digest` closes the gap between showing a batch and acting on it. Every review surface
    has one, and another client may amend, withdraw, or commit inside it. A caller that passes the
    digest back gets its action refused if the batch moved; a caller that does not keeps exactly
    the behaviour it always had. It is computed at read time, never stored, and covers each row's
    id, status, and stored candidate — not the read-time projections above, which no writer owns.
    """

    batch_id: str
    candidates: list[ImportReviewRow]
    batch_digest: str = ""


class CommitImportResult(BaseModel):
    """Selective commit outcome, including unresolved accepted interactions."""

    batch_id: str
    committed_ids: list[str] = Field(default_factory=list)
    unresolved_ids: list[str] = Field(default_factory=list)
    skipped_ids: list[str] = Field(default_factory=list)


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: UTF-8 bytes one agent-extracted observation may distil into `text`.
MAX_OBSERVATION_TEXT_BYTES: Final = 4 * 1024

#: UTF-8 bytes one agent-extracted trait may distil into `value`.
MAX_TRAIT_VALUE_BYTES: Final = 2 * 1024

#: UTF-8 bytes one agent-extracted trait may distil into `evidence_note`.
MAX_TRAIT_EVIDENCE_NOTE_BYTES: Final = 2 * 1024

#: Characters a relationship candidate's free-form type text may carry.
MAX_RELATIONSHIP_TYPE_CHARS: Final = 256

#: Characters a batch-local person reference on an M17 candidate may carry.
MAX_CANDIDATE_REF_CHARS: Final = 256

#: Characters a batch-local evidence reference or a durable evidence id may carry.
#:
#: A durable id is *format-opaque*: the ceiling bounds what one request may submit and nothing
#: more. Nothing here requires a ULID shape, case-folds, or normalizes, so a restored or
#: hand-authored id such as `obs-1` stays addressable.
MAX_EVIDENCE_REF_CHARS: Final = MAX_EVIDENCE_REFERENCE_CHARS


def _within_bytes(limit: int) -> Callable[[str], str]:
    """Return a validator that bounds a field by UTF-8 bytes without echoing its value.

    The field limits are byte budgets rather than character counts because what they protect
    is storage and read size, and because a character cap would let one script cost four times
    another. The refusal states only the limit: the rejected text is untrusted extraction
    output and must not travel back out through a diagnostic.
    """

    def check(value: str) -> str:
        if len(value.encode("utf-8")) > limit:
            raise ValueError(f"value exceeds the {limit} byte limit for this field")
        return value

    return check


def _normalizable_relationship_type(value: str) -> str:
    """Reject exactly the relationship type text `SetRelationship` itself would reject.

    Normalization happens at commit through the existing relationship contract, so the staged
    candidate keeps the agent's own wording. What is checked here is only the one condition
    that would make the durable write fail: text with no word character to normalize.
    """
    if not normalize_relationship_type(value):
        raise ValueError("relationship type must contain at least one word character")
    return value


ObservationText = Annotated[NonBlank, AfterValidator(_within_bytes(MAX_OBSERVATION_TEXT_BYTES))]
TraitValue = Annotated[NonBlank, AfterValidator(_within_bytes(MAX_TRAIT_VALUE_BYTES))]
TraitEvidenceNote = Annotated[NonBlank, AfterValidator(_within_bytes(MAX_TRAIT_EVIDENCE_NOTE_BYTES))]
CandidateRef = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CANDIDATE_REF_CHARS),
]
#: Attribution on a fact or affiliation candidate, bounded by the domain that owns the field.
#:
#: The bound sits on the model rather than in the extraction budgets because those budgets are
#: conditional: `contains_extraction_candidate` selects them only for a batch that names an M17
#: type, so a legacy fact-only batch would otherwise carry an unbounded attribution. The same
#: domain type bounds the *persisted* candidate, so a restored bundle cannot reintroduce what
#: staging refuses. See `MAX_STATED_BY_CHARS`.
StatedBy = StatedByText
RelationshipTypeText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_RELATIONSHIP_TYPE_CHARS),
    AfterValidator(_normalizable_relationship_type),
]
def _non_blank_token(value: str) -> str:
    """Accept one opaque token, checking it is not blank without rewriting it.

    Every other bounded string here is stripped, which is right for text a person typed. An
    evidence reference is not text: it is an identity, matched exactly against a durable id whose
    own contract — the released bundle `Identifier` — accepts any non-blank string, whitespace
    included. Stripping would therefore make a legitimately restored id unciteable, and worse,
    could silently resolve it to a *different* record whose id happens to be the trimmed form.
    """
    if not value.strip():
        raise ValueError("an evidence reference must not be blank")
    return value


EvidenceReference = Annotated[
    str,
    StringConstraints(max_length=MAX_EVIDENCE_REF_CHARS),
    AfterValidator(_non_blank_token),
]

#: A durable record id a candidate names directly, preserved exactly as the caller gave it.
#:
#: Not `NonBlank`: that strips, and an id is an identity rather than text a person typed. The
#: bundle's own `Identifier` contract accepts any non-blank string, whitespace included, so a
#: restored id can legitimately carry it — and `find_groups` hands that exact id back. Trimming
#: one here would make the group the user confirmed unfindable at commit, or resolve it to a
#: different record whose id happens to be the trimmed form.
#:
#: Unbounded in length for the same reason, and unlike `EvidenceReference`. That ceiling is the
#: caller-supplied *label* half of the evidence contract, which a durable group reference has no
#: equivalent of: there is nothing free-form to bound here, only an id that either matches a
#: stored row or does not. Capping it would refuse an id this installation can legitimately hold
#: after a restore, while protecting nothing — a group or membership candidate always opts the
#: request into the extraction budget, where every string is already held to 8 KiB.
DurableIdentifier = Annotated[str, AfterValidator(_non_blank_token)]


def check_candidate_period(valid_from: date | None, valid_to: date | None) -> None:
    """Hold a candidate's date range to the rule its durable record already enforces.

    Every dated record here stores a `ValidityPeriod`, which refuses a start after its end. That
    check is constructed at commit, inside the transaction, after earlier candidates in the same
    batch have already written — so a reversed range staged today is a Pydantic error raised out
    of `commit_import` tomorrow, with an immutable batch that can never be completed. Reusing the
    domain type here refuses it at the door instead, and refuses it identically, without a second
    copy of the rule drifting from the first.
    """
    ValidityPeriod(valid_from=valid_from, valid_to=valid_to)


class CandidateAlias(BaseModel):
    """Strict alias accepted in a staged person candidate."""

    model_config = ConfigDict(extra="forbid")

    value: NonBlank
    kind: AliasKind = AliasKind.OTHER
    lang: str | None = None
    script: str | None = None


class PersonCandidateInput(BaseModel):
    """Strict batch-local person candidate."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["person"]
    ref: NonBlank
    name: NonBlank
    aliases: list[CandidateAlias]
    summary: str | None = None
    message_id: str | None = None
    date: datetime | None = None


class InteractionCandidateInput(BaseModel):
    """Strict interaction candidate referencing people in the same batch."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["interaction"]
    summary: NonBlank
    participant_refs: list[NonBlank]
    date: datetime
    channel: str | None = None
    message_id: str | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    #: An optional batch-local label a trait in the same request may cite as evidence. It is
    #: purely addressing: it is removed during staging, never persisted, and an interaction
    #: candidate without one is byte-for-byte the candidate it always was.
    evidence_ref: EvidenceReference | None = None


class AffiliationCandidateInput(BaseModel):
    """Strict affiliation candidate referencing one batch-local person."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["affiliation"]
    person_ref: NonBlank
    org: NonBlank
    role: NonBlank
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    #: Who asserted this affiliation, forwarded into the record's existing provenance.
    #: See `FactCandidateInput.stated_by`; absent when the attribution is unknown.
    stated_by: StatedBy | None = None

    @model_validator(mode="after")
    def _check_period(self) -> AffiliationCandidateInput:
        check_candidate_period(self.valid_from, self.valid_to)
        return self


class FactCandidateInput(BaseModel):
    """Strict fact candidate referencing one batch-local person."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["fact"]
    person_ref: NonBlank
    predicate: NonBlank
    value: NonBlank
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    #: Who asserted this claim, forwarded into the record's existing `Provenance.stated_by`.
    #:
    #: This is assertion attribution, and it is none of the three things next to it: `source` is
    #: the process that wrote the row, `session` the process run, and an M18 `source_session_id`
    #: the receipt for the artifact that was read. A CV saying someone is analytical is that
    #: person's own claim about themselves, not a verified characteristic, and recording who said
    #: it is what keeps the two apart. Unknown attribution stays absent: never invent a speaker.
    stated_by: StatedBy | None = None

    @model_validator(mode="after")
    def _check_period(self) -> FactCandidateInput:
        check_candidate_period(self.valid_from, self.valid_to)
        return self


class ObservationCandidateInput(BaseModel):
    """Strict observation candidate: something an agent saw happen in one source."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["observation"]
    person_ref: CandidateRef
    text: ObservationText
    observed_at: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    #: See `InteractionCandidateInput.evidence_ref`: a batch-local label, not stored state.
    evidence_ref: EvidenceReference | None = None


class TraitCandidateInput(BaseModel):
    """Strict trait candidate, held to stronger evidence than a direct `record_trait` write.

    `RecordTraitInput` lets both `evidence_note` and `confidence` default, which is right for a
    person stating something about someone they know. An inference distilled out of unstructured
    material is a weaker claim, so this boundary requires the agent to say what the inference
    rests on and how sure it is rather than letting silence read as certainty.

    M18.3 adds the id-based half of that grounding. `evidence_refs` names observation and
    interaction candidates in this same request — an agent cannot know their staging ids, so it
    addresses them by its own labels and staging rewrites them — while `evidence_ids` names
    records already in the store. Both stay optional: `evidence_note` was never a placeholder for
    them, and a trait drawn from material that produced no durable record is still a trait.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["trait"]
    person_ref: CandidateRef
    category: TraitCategory
    value: TraitValue
    evidence_note: TraitEvidenceNote
    confidence: Confidence
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    evidence_ids: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_evidence(self) -> TraitCandidateInput:
        """Bound the grounding one trait may assert, naming no rejected value.

        Uniqueness is per collection because the two name different things — a batch-local label
        and a durable record — and a value legitimately appearing in both is not a repetition.
        The budget spans them because it bounds what one trait's retrieval has to read, which
        does not care which half a citation came from.
        """
        for field_name, values in (
            ("evidence_refs", self.evidence_refs),
            ("evidence_ids", self.evidence_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not repeat a reference")
        if len(self.evidence_refs) + len(self.evidence_ids) > MAX_TRAIT_EVIDENCE_LINKS:
            raise ValueError(
                f"a trait cites at most {MAX_TRAIT_EVIDENCE_LINKS} references and durable evidence ids combined"
            )
        return self


class RelationshipCandidateInput(BaseModel):
    """Strict relationship candidate between two batch-local people.

    There is deliberately no `sensitivity` field. The durable `Relationship` model and the
    ordinary graph reads carry no disclosure level, so a candidate-only one would be discarded
    at commit while implying a protection the graph cannot enforce. Because the model forbids
    extras, an attempt to send one fails loudly instead of being silently dropped: an elevated
    relationship stays out of the graph rather than entering it downgraded.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["relationship"]
    from_ref: CandidateRef
    to_ref: CandidateRef
    relationship_type: RelationshipTypeText
    confidence: Confidence | None = None


class GroupCandidateInput(BaseModel):
    """Strict group candidate: one identified context the source named, addressed by `ref`.

    A group candidate never resolves by name. M28.1 offers no get-or-create by name because
    two groups called "Class 1" are different rooms until somebody says otherwise, and staging
    is not the place that judgement gets made silently. `group_id` is therefore the only way to
    reuse a stored group: the agent resolves it in conversation through `find_groups` and passes
    the id it was given. Without one, commit creates a new group, which is the honest outcome
    when nobody has confirmed which existing group this is.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["group"]
    #: The batch-local label memberships in this same request cite through `group_ref`.
    ref: CandidateRef
    name: GroupName
    kind: GroupKind
    #: An existing organization this group sits under. Placement is context, never employment.
    organization_id: DurableIdentifier | None = None
    #: An existing group to record memberships against instead of creating another one.
    group_id: DurableIdentifier | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    #: Who said this group exists. See `FactCandidateInput.stated_by`; absent when unknown.
    stated_by: StatedBy | None = None


class MembershipCandidateInput(BaseModel):
    """Strict membership candidate placing one batch-local person in one batch-local group.

    Dates stay exactly as reported. `temporal_basis` defaults the way `AddGroupMembershipInput`
    already defaults it — no dates is `unknown`, any date is `period`, and `ongoing` is never
    inferred — so a source that did not say when produces a membership that does not claim to
    know. Filling an unknown bound to make a later lookup return `classmates` is the one thing
    this candidate exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["group_membership"]
    person_ref: CandidateRef
    #: The `ref` of a group candidate in this same request.
    group_ref: CandidateRef
    role: MembershipRole = MembershipRole.MEMBER
    valid_from: date | None = None
    valid_to: date | None = None
    temporal_basis: TemporalBasis | None = None
    confidence: Confidence | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    #: Who asserted this membership. See `FactCandidateInput.stated_by`.
    stated_by: StatedBy | None = None

    @model_validator(mode="after")
    def _check_basis(self) -> MembershipCandidateInput:
        """Refuse dates, or a declared basis, that the durable write would reject.

        Only an explicit basis can contradict anything — a defaulted one is derived from these
        same dates. Left to commit, either failure would raise mid-transaction after earlier
        candidates in the batch had already written, and until then review would show a
        membership whose dates say one thing and whose basis says another.
        """
        check_candidate_period(self.valid_from, self.valid_to)
        check_temporal_basis(
            resolve_temporal_basis(self.temporal_basis, self.valid_from, self.valid_to),
            self.valid_from,
            self.valid_to,
        )
        return self


CandidateInput = Annotated[
    PersonCandidateInput
    | InteractionCandidateInput
    | AffiliationCandidateInput
    | FactCandidateInput
    | ObservationCandidateInput
    | TraitCandidateInput
    | RelationshipCandidateInput
    | GroupCandidateInput
    | MembershipCandidateInput,
    Field(discriminator="type"),
]

CANDIDATE_MODELS: dict[str, type[BaseModel]] = {
    "person": PersonCandidateInput,
    "interaction": InteractionCandidateInput,
    "affiliation": AffiliationCandidateInput,
    "fact": FactCandidateInput,
    "observation": ObservationCandidateInput,
    "trait": TraitCandidateInput,
    "relationship": RelationshipCandidateInput,
    "group": GroupCandidateInput,
    "group_membership": MembershipCandidateInput,
}

#: The candidate types M17 introduced, plus M28.3's. A staging request that uses one of them
#: opts into the bounded extraction contract; a request built only from the four released types
#: does not. Groups and memberships belong here because they are distilled from unstructured
#: material exactly as an observation is, and an unbounded batch of them would be the one
#: extraction path the budgets do not reach.
EXTRACTION_CANDIDATE_TYPES: Final = frozenset(
    {"observation", "trait", "relationship", "group", "group_membership"}
)


def contains_extraction_candidate(candidates: list[Any]) -> bool:
    """Return whether a raw request opts into an M17 candidate type.

    This reads the untrusted request before validation, because the bounds it selects are the
    ones that must hold *before* anything parses or stages an oversized payload.
    """
    return any(
        isinstance(candidate, dict) and candidate.get("type") in EXTRACTION_CANDIDATE_TYPES
        for candidate in candidates
    )
