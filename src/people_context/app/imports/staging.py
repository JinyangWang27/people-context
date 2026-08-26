"""Candidate validation, reference rewriting, matching, and atomic staging."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from people_context.app.imports.limits import (
    STAGED_PAYLOAD_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    UNBOUNDED_IMPORT_BUDGET,
    ImportBudget,
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
    PersonCandidateInput,
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
    ) -> ImportBatchResult:
        """Stage one batch, refusing an over-budget one before any row is persisted.

        ``budget`` defaults to the released unbounded contract. When a caller supplies one the
        count is checked before the batch is validated and the payload is measured while rows
        are built, so an over-budget source stops the work instead of being discovered after it.
        """
        limits = budget or UNBOUNDED_IMPORT_BUDGET
        self._reject_excess_candidates(len(candidates), limits)
        validated = self._validate(candidates)
        batch_id = new_id()
        references = self._references(validated)
        rows = self._rows(batch_id, source, validated, references, limits)
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
            row = self._row(batch_id, source, candidate, references)
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
    ) -> StagedImportRow:
        staged = candidate.model_dump(mode="json", exclude_none=True)
        if isinstance(candidate, PersonCandidateInput):
            row_id = references[candidate.ref]
            staged.pop("ref")
            handles = [alias.value for alias in candidate.aliases if alias.kind == AliasKind.HANDLE]
            matched = self._match_existing([*handles, candidate.name])
            staged["matched_person_id"] = matched.id if matched else None
        else:
            row_id = new_id()
            if isinstance(candidate, InteractionCandidateInput):
                staged.pop("participant_refs")
                staged["participant_candidate_ids"] = [references[ref] for ref in candidate.participant_refs]
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
        normalized_source = source.strip()
        if not normalized_source:
            raise _invalid_candidates("source must not be blank")
        return self._stager.execute(f"import/agent:{normalized_source}", candidates)


def _candidate_refs(candidate: CandidateInput) -> list[str]:
    if isinstance(candidate, InteractionCandidateInput):
        return candidate.participant_refs
    if isinstance(candidate, (AffiliationCandidateInput, FactCandidateInput)):
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
