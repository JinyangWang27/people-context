"""Header-only extraction, staging review, and selective import commit."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from people_context.app.record import AliasInput, RememberPerson, RememberPersonInput
from people_context.app.record_fact import RecordFact, RecordFactInput
from people_context.app.record_interaction import RecordInteraction, RecordInteractionInput
from people_context.app.set_affiliation import SetAffiliation, SetAffiliationInput
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import Confidence, Sensitivity, new_id, normalize_name
from people_context.ports.clock import Clock
from people_context.ports.imports import ImportExtractor, ImportStagingStore, StagedImportRow
from people_context.ports.repository import PersonReader


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
_CANDIDATES_ADAPTER = TypeAdapter(list[CandidateInput])
_CANDIDATE_MODELS = {
    "person": PersonCandidateInput,
    "interaction": InteractionCandidateInput,
    "affiliation": AffiliationCandidateInput,
    "fact": FactCandidateInput,
}


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
    ) -> ImportBatchResult:
        validated = self._validate(candidates)
        batch_id = new_id()
        references = self._references(validated)
        rows = [self._row(batch_id, source, candidate, references) for candidate in validated]
        self._staging.stage_batch(rows)
        return ImportBatchResult(
            batch_id=batch_id,
            candidate_count=len(rows),
            skipped_message_ids=skipped_message_ids or [],
            skipped_without_id=skipped_without_id,
            skipped_cards=skipped_cards or [],
        )

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
        return {
            candidate.ref: new_id()
            for candidate in candidates
            if isinstance(candidate, PersonCandidateInput)
        }

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
        allowed_types=list(_CANDIDATE_MODELS),
        valid_fields={
            name: list(model.model_fields)
            for name, model in _CANDIDATE_MODELS.items()
        },
    )


class ImportContent:
    """Extract a source, match existing people, and stage candidate JSON atomically."""

    def __init__(
        self,
        people: PersonReader,
        extractor: ImportExtractor,
        staging: ImportStagingStore,
        clock: Clock,
        candidate_stager: CandidateStager | None = None,
    ) -> None:
        self._people = people
        self._extractor = extractor
        self._candidate_stager = candidate_stager or CandidateStager(people, staging, clock)

    def execute(self, source_type: str, content: str | None = None, path: str | None = None) -> ImportBatchResult:
        """Stage header-derived people followed by interaction candidates."""
        source = f"import/{source_type}"
        extracted = self._extractor.extract(
            source_type,
            content=content,
            path=path,
            self_addresses=self._self_addresses(),
        )
        if not extracted.people and not extracted.interactions and not extracted.candidates:
            raise ImportPipelineError(
                "no_candidates",
                "source contains no external import candidates",
                skipped_cards=extracted.skipped_cards,
            )
        candidates = list(extracted.candidates)
        if not candidates:
            for candidate in extracted.people:
                aliases = [{"value": candidate.email, "kind": AliasKind.HANDLE.value}]
                aliases.extend(
                    {"value": name, "kind": AliasKind.OTHER.value}
                    for name in candidate.alternate_names
                )
                candidates.append(
                    {
                        "type": "person",
                        "ref": candidate.email,
                        "name": candidate.name,
                        "aliases": aliases,
                        "message_id": candidate.message_id,
                        "date": candidate.date,
                    }
                )
            candidates.extend(
                {
                    "type": "interaction",
                    "summary": candidate.subject or "Email correspondence",
                    "participant_refs": candidate.participant_emails,
                    "channel": "email",
                    "message_id": candidate.message_id,
                    "date": candidate.occurred_at,
                }
                for candidate in extracted.interactions
            )
        return self._candidate_stager.execute(
            source,
            candidates,
            skipped_message_ids=extracted.skipped_message_ids,
            skipped_without_id=extracted.skipped_without_id,
            skipped_cards=extracted.skipped_cards,
        )

    def _self_addresses(self) -> set[str]:
        person = self._people.get_self()
        if person is None:
            return set()
        return {alias.value for alias in person.aliases if alias.kind == AliasKind.HANDLE}


class ReviewImport:
    """Return review-safe rows for one known staging batch."""

    def __init__(self, staging: ImportStagingStore) -> None:
        self._staging = staging

    def execute(self, batch_id: str) -> ImportReviewResult:
        rows = self._staging.list_batch(batch_id)
        if not rows:
            raise ImportPipelineError("batch_not_found", f"import batch not found: {batch_id}", batch_id=batch_id)
        return ImportReviewResult(
            batch_id=batch_id,
            candidates=[
                ImportReviewRow(id=row.id, source=row.source, status=row.status, candidate=row.candidate)
                for row in rows
            ],
        )


class CommitImport:
    """Commit accepted people first, then all resolvable accepted interactions."""

    def __init__(
        self,
        people: PersonReader,
        staging: ImportStagingStore,
        remember_person: RememberPerson,
        record_interaction: RecordInteraction,
        set_affiliation: SetAffiliation,
        record_fact: RecordFact,
    ) -> None:
        self._people = people
        self._staging = staging
        self._remember_person = remember_person
        self._record_interaction = record_interaction
        self._set_affiliation = set_affiliation
        self._record_fact = record_fact

    def execute(self, batch_id: str, accepted_ids: list[str]) -> CommitImportResult:
        rows = self._staging.list_batch(batch_id)
        if not rows:
            raise ImportPipelineError("batch_not_found", f"import batch not found: {batch_id}", batch_id=batch_id)
        by_id = {row.id: row for row in rows}
        invalid_ids = sorted(set(accepted_ids) - by_id.keys())
        if invalid_ids:
            raise ImportPipelineError(
                "candidate_not_in_batch",
                "accepted candidate does not belong to batch",
                batch_id=batch_id,
                candidate_ids=invalid_ids,
            )
        accepted = set(accepted_ids)
        committed: list[str] = []
        skipped = [row.id for row in rows if row.id in accepted and row.status == "committed"]
        resolution = self._existing_resolution(rows)
        for row in rows:
            if row.id not in accepted or row.status == "committed" or row.candidate.get("type") != "person":
                continue
            resolution[row.id] = self._commit_person(row)
            committed.append(row.id)
        unresolved: list[str] = []
        for row in rows:
            if row.id not in accepted or row.status == "committed":
                continue
            candidate_type = row.candidate.get("type")
            if candidate_type not in {"affiliation", "fact"}:
                continue
            person_candidate_id = row.candidate["person_candidate_id"]
            person_id = resolution.get(person_candidate_id)
            if person_id is None:
                unresolved.append(row.id)
                continue
            if candidate_type == "affiliation":
                self._set_affiliation.execute(
                    SetAffiliationInput(
                        person_id=person_id,
                        org=row.candidate["org"],
                        role=row.candidate["role"],
                        valid_from=row.candidate.get("valid_from"),
                        valid_to=row.candidate.get("valid_to"),
                        confidence=row.candidate.get("confidence"),
                        source=row.source,
                        session=row.candidate.get("message_id"),
                    )
                )
            else:
                self._record_fact.execute(
                    RecordFactInput(
                        person_id=person_id,
                        predicate=row.candidate["predicate"],
                        value=row.candidate["value"],
                        valid_from=row.candidate.get("valid_from"),
                        valid_to=row.candidate.get("valid_to"),
                        confidence=row.candidate.get("confidence"),
                        sensitivity=row.candidate.get("sensitivity", "personal"),
                        source=row.source,
                        session=row.candidate.get("message_id"),
                    )
                )
            committed.append(row.id)
        for row in rows:
            if row.id not in accepted or row.status == "committed" or row.candidate.get("type") != "interaction":
                continue
            refs = row.candidate["participant_candidate_ids"]
            participant_ids = [resolution[ref] for ref in refs if ref in resolution]
            if len(participant_ids) != len(refs):
                unresolved.append(row.id)
                continue
            self._record_interaction.execute(
                RecordInteractionInput(
                    summary=row.candidate["summary"],
                    participant_ids=participant_ids,
                    occurred_at=row.candidate["date"],
                    channel=row.candidate.get("channel"),
                    sensitivity=row.candidate.get("sensitivity", "personal"),
                    source=row.source,
                    session=row.candidate.get("message_id"),
                )
            )
            committed.append(row.id)
        self._staging.mark_committed(committed)
        return CommitImportResult(
            batch_id=batch_id,
            committed_ids=committed,
            unresolved_ids=unresolved,
            skipped_ids=skipped,
        )

    def _existing_resolution(self, rows: list[StagedImportRow]) -> dict[str, str]:
        resolution: dict[str, str] = {}
        for row in rows:
            if row.candidate.get("type") != "person":
                continue
            matched_id = row.candidate.get("matched_person_id")
            if matched_id and self._people.get(matched_id) is not None:
                resolution[row.id] = matched_id
                continue
            if row.status == "committed":
                values = [alias["value"] for alias in row.candidate.get("aliases", [])]
                values.append(row.candidate["name"])
                for value in values:
                    matches = self._people.find_by_normalized_name(normalize_name(value))
                    if len(matches) == 1:
                        resolution[row.id] = matches[0].id
                        break
        return resolution

    def _commit_person(self, row: StagedImportRow) -> str:
        candidate = row.candidate
        matched_id = candidate.get("matched_person_id")
        matched = self._people.get(matched_id) if matched_id else None
        aliases = [AliasInput.model_validate(alias) for alias in candidate["aliases"]]
        if matched is not None:
            aliases.insert(0, AliasInput(value=candidate["name"]))
            name = matched.canonical_name
        else:
            name = candidate["name"]
        result = self._remember_person.execute(
            RememberPersonInput(
                name=name,
                aliases=aliases,
                summary=candidate.get("summary"),
                source=row.source,
                session=candidate.get("message_id"),
            )
        )
        return result.person.id
