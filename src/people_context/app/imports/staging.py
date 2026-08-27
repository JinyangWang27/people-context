"""Candidate validation, reference rewriting, matching, and atomic staging."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from people_context.app.imports.identity import match_person_candidate
from people_context.app.imports.limits import (
    STAGED_PAYLOAD_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    UNBOUNDED_IMPORT_BUDGET,
    ImportBudget,
    enforce_extraction_request_limits,
    resource_limit_error,
)
from people_context.app.imports.models import (
    CANDIDATE_MODELS,
    AffiliationCandidateInput,
    CandidateInput,
    FactCandidateInput,
    ImportBatchResult,
    ImportPipelineError,
    InteractionCandidateInput,
    ObservationCandidateInput,
    PersonCandidateInput,
    RelationshipCandidateInput,
    TraitCandidateInput,
    contains_extraction_candidate,
)
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import new_id, normalize_name
from people_context.ports.clock import Clock
from people_context.ports.imports import ImportStagingStore, StagedImportRow
from people_context.ports.repository import PersonReader

_CANDIDATES_ADAPTER = TypeAdapter(list[CandidateInput])


class CandidateStager:
    """Validate, match, rewrite references, and atomically stage one candidate batch."""

    def __init__(self, people: PersonReader, staging: ImportStagingStore, clock: Clock) -> None:
        self._people = people
        self._staging = staging
        self._clock = clock

    def execute(
        self,
        source: str,
        candidates: list[dict[str, Any]],
        *,
        skipped_message_ids: list[str] | None = None,
        skipped_without_id: int = 0,
        skipped_cards: list[dict[str, int | str]] | None = None,
        budget: ImportBudget | None = None,
        strict_identity: bool = False,
    ) -> ImportBatchResult:
        """Stage one batch, refusing an over-budget one before any row is persisted.

        ``budget`` defaults to the released unbounded contract. When a caller supplies one the
        count is checked before the batch is validated and the payload is measured while rows
        are built, so an over-budget source stops the work instead of being discovered after it.

        ``strict_identity`` selects the ambiguity-preserving matcher. It is off by default so
        that every caller that predates M17 — the source importers and `pctx init` among them —
        keeps staging the person rows it always staged.
        """
        limits = budget or UNBOUNDED_IMPORT_BUDGET
        self._reject_excess_candidates(len(candidates), limits)
        validated = self._validate(candidates)
        batch_id = new_id()
        references = self._references(validated)
        rows = self._rows(batch_id, source, validated, references, limits, strict_identity)
        self._staging.stage_batch(rows)
        return ImportBatchResult(
            batch_id=batch_id,
            candidate_count=len(rows),
            skipped_message_ids=skipped_message_ids or [],
            skipped_without_id=skipped_without_id,
            skipped_cards=skipped_cards or [],
        )

    @staticmethod
    def _reject_excess_candidates(count: int, limits: ImportBudget) -> None:
        if limits.max_candidates is not None and count > limits.max_candidates:
            raise resource_limit_error(
                TOO_MANY_CANDIDATES,
                f"source produces more than the {limits.max_candidates} candidates this command stages",
                limit=limits.max_candidates,
            )

    def _rows(
        self,
        batch_id: str,
        source: str,
        candidates: list[CandidateInput],
        references: dict[str, str],
        limits: ImportBudget,
        strict_identity: bool,
    ) -> list[StagedImportRow]:
        """Build every staged row, stopping the moment the persisted payload exceeds budget.

        The measurement is the same one the storage-level preflight computes later — staged
        `source` plus the exact candidate JSON the store writes — so a batch this method
        accepts is a batch review and commit can afford to read back.
        """
        source_bytes = len(source.encode("utf-8"))
        payload_bytes = 0
        rows: list[StagedImportRow] = []
        for candidate in candidates:
            row = self._row(batch_id, source, candidate, references, strict_identity)
            payload_bytes += source_bytes + len(json.dumps(row.candidate, ensure_ascii=False).encode("utf-8"))
            if limits.max_staged_payload_bytes is not None and payload_bytes > limits.max_staged_payload_bytes:
                raise resource_limit_error(
                    STAGED_PAYLOAD_TOO_LARGE,
                    "staged candidates exceed the "
                    f"{limits.max_staged_payload_bytes} byte reviewable payload limit for this command",
                    limit=limits.max_staged_payload_bytes,
                )
            rows.append(row)
        return rows

    def _validate(self, candidates: list[dict[str, Any]]) -> list[CandidateInput]:
        if not candidates:
            raise _invalid_candidates("candidates must not be empty")
        try:
            validated = _CANDIDATES_ADAPTER.validate_python(candidates)
        except ValidationError as exc:
            raise _invalid_candidates(
                "candidate validation failed",
                details=exc.errors(include_url=False, include_context=False, include_input=False),
            ) from exc
        references: dict[str, int] = {}
        for index, candidate in enumerate(validated):
            if isinstance(candidate, PersonCandidateInput):
                if candidate.ref in references:
                    raise _invalid_candidates(
                        "duplicate person reference",
                        details=[
                            {
                                "type": "value_error",
                                "loc": [index, "ref"],
                                "msg": f"duplicate person ref: {candidate.ref}",
                            }
                        ],
                    )
                references[candidate.ref] = index
        for index, candidate in enumerate(validated):
            refs = _candidate_refs(candidate)
            unknown = sorted(set(refs) - references.keys())
            if unknown:
                raise _invalid_candidates(
                    "unknown person reference",
                    details=[
                        {
                            "type": "value_error",
                            "loc": [index],
                            "msg": f"unknown person refs: {', '.join(unknown)}",
                        }
                    ],
                )
            if isinstance(candidate, RelationshipCandidateInput) and candidate.from_ref == candidate.to_ref:
                raise _invalid_candidates(
                    "relationship endpoints must be different people",
                    details=[
                        {
                            "type": "value_error",
                            "loc": [index, "to_ref"],
                            "msg": "from_ref and to_ref must name different batch-local people",
                        }
                    ],
                )
        return validated

    @staticmethod
    def _references(candidates: list[CandidateInput]) -> dict[str, str]:
        return {candidate.ref: new_id() for candidate in candidates if isinstance(candidate, PersonCandidateInput)}

    def _row(
        self,
        batch_id: str,
        source: str,
        candidate: CandidateInput,
        references: dict[str, str],
        strict_identity: bool,
    ) -> StagedImportRow:
        staged = candidate.model_dump(mode="json", exclude_none=True)
        if isinstance(candidate, PersonCandidateInput):
            row_id = references[candidate.ref]
            staged.pop("ref")
            handles = [alias.value for alias in candidate.aliases if alias.kind == AliasKind.HANDLE]
            tokens = [*handles, candidate.name]
            if strict_identity:
                match = match_person_candidate(self._people, tokens)
                staged["matched_person_id"] = match.person_id
                staged["match_disposition"] = match.disposition.value
                staged["match_count"] = match.match_count
            else:
                matched = self._match_existing(tokens)
                staged["matched_person_id"] = matched.id if matched else None
        else:
            row_id = new_id()
            if isinstance(candidate, InteractionCandidateInput):
                staged.pop("participant_refs")
                staged["participant_candidate_ids"] = [references[ref] for ref in candidate.participant_refs]
            elif isinstance(candidate, RelationshipCandidateInput):
                staged.pop("from_ref")
                staged.pop("to_ref")
                staged["from_candidate_id"] = references[candidate.from_ref]
                staged["to_candidate_id"] = references[candidate.to_ref]
            else:
                staged.pop("person_ref")
                staged["person_candidate_id"] = references[candidate.person_ref]
        return StagedImportRow(
            id=row_id,
            batch_id=batch_id,
            source=source,
            candidate=staged,
            status="pending",
            created_at=self._clock.now(),
        )

    def _match_existing(self, values: list[str]) -> Person | None:
        for value in values:
            matches = self._people.find_by_normalized_name(normalize_name(value))
            if len(matches) == 1:
                return matches[0]
        return None


class StageCandidates:
    """Stage strict agent-generated candidates with durable agent provenance."""

    def __init__(self, stager: CandidateStager) -> None:
        self._stager = stager

    def execute(self, source: str, candidates: list[dict[str, Any]]) -> ImportBatchResult:
        """Stage one agent request, bounding it first when it opts into an M17 candidate type.

        The extraction bounds and the ambiguity-preserving matcher are selected by the same
        condition, and it is read off the raw request rather than the validated one: a request
        that names an M17 type is an extraction request whether or not it turns out to be
        well-formed, and both decisions must be made before anything is parsed or staged.
        """
        normalized_source = source.strip()
        if not normalized_source:
            raise _invalid_candidates("source must not be blank")
        extraction = contains_extraction_candidate(candidates)
        if extraction:
            enforce_extraction_request_limits(normalized_source, candidates)
        return self._stager.execute(
            f"import/agent:{normalized_source}",
            candidates,
            strict_identity=extraction,
        )


def _candidate_refs(candidate: CandidateInput) -> list[str]:
    if isinstance(candidate, InteractionCandidateInput):
        return candidate.participant_refs
    if isinstance(candidate, RelationshipCandidateInput):
        return [candidate.from_ref, candidate.to_ref]
    if isinstance(
        candidate,
        (AffiliationCandidateInput, FactCandidateInput, ObservationCandidateInput, TraitCandidateInput),
    ):
        return [candidate.person_ref]
    return []


def _invalid_candidates(message: str, **details: Any) -> ImportPipelineError:
    validation_details = details.pop(
        "details",
        [{"type": "value_error", "loc": [], "msg": message}],
    )
    return ImportPipelineError(
        "invalid_candidates",
        message,
        **details,
        details=validation_details,
        allowed_types=list(CANDIDATE_MODELS),
        valid_fields={name: list(model.model_fields) for name, model in CANDIDATE_MODELS.items()},
    )
