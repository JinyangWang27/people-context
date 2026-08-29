"""Validated import candidates and stable workflow results."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from people_context.domain.person import AliasKind
from people_context.domain.relationship_vocabulary import normalize_relationship_type
from people_context.domain.shared import Confidence, Sensitivity
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


class ImportReviewRow(BaseModel):
    """Review-safe staging row."""

    id: str
    source: str
    status: str
    candidate: dict[str, Any]


class ImportReviewResult(BaseModel):
    """All candidates and statuses for one batch."""

    batch_id: str
    candidates: list[ImportReviewRow]


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
RelationshipTypeText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_RELATIONSHIP_TYPE_CHARS),
    AfterValidator(_normalizable_relationship_type),
]
EvidenceReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EVIDENCE_REF_CHARS),
]


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


CandidateInput = Annotated[
    PersonCandidateInput
    | InteractionCandidateInput
    | AffiliationCandidateInput
    | FactCandidateInput
    | ObservationCandidateInput
    | TraitCandidateInput
    | RelationshipCandidateInput,
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
}

#: The candidate types M17 introduced. A staging request that uses one of them opts into the
#: bounded extraction contract; a request built only from the four released types does not.
EXTRACTION_CANDIDATE_TYPES: Final = frozenset({"observation", "trait", "relationship"})


def contains_extraction_candidate(candidates: list[Any]) -> bool:
    """Return whether a raw request opts into an M17 candidate type.

    This reads the untrusted request before validation, because the bounds it selects are the
    ones that must hold *before* anything parses or stages an oversized payload.
    """
    return any(
        isinstance(candidate, dict) and candidate.get("type") in EXTRACTION_CANDIDATE_TYPES
        for candidate in candidates
    )
