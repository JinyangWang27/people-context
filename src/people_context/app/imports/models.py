"""Validated import candidates and stable workflow results."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from people_context.domain.person import AliasKind
from people_context.domain.shared import Confidence, Sensitivity


class ImportPipelineError(Exception):
    """Raised for staging-batch and accepted-candidate validation failures."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class ImportBatchResult(BaseModel):
    """Summary of one atomically staged extraction batch."""

    batch_id: str
    candidate_count: int
    skipped_message_ids: list[str] = Field(default_factory=list)
    skipped_without_id: int = 0
    skipped_cards: list[dict[str, int | str]] = Field(default_factory=list)


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


CandidateInput = Annotated[
    PersonCandidateInput | InteractionCandidateInput | AffiliationCandidateInput | FactCandidateInput,
    Field(discriminator="type"),
]

CANDIDATE_MODELS: dict[str, type[BaseModel]] = {
    "person": PersonCandidateInput,
    "interaction": InteractionCandidateInput,
    "affiliation": AffiliationCandidateInput,
    "fact": FactCandidateInput,
}
