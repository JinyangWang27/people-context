"""Header-only extraction, staging review, and selective import commit."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from people_context.app.record import AliasInput, RememberPerson, RememberPersonInput
from people_context.app.record_interaction import RecordInteraction, RecordInteractionInput
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import new_id, normalize_name
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


class ImportContent:
    """Extract a source, match existing people, and stage candidate JSON atomically."""

    def __init__(
        self,
        people: PersonReader,
        extractor: ImportExtractor,
        staging: ImportStagingStore,
        clock: Clock,
    ) -> None:
        self._people = people
        self._extractor = extractor
        self._staging = staging
        self._clock = clock

    def execute(self, source_type: str, content: str | None = None, path: str | None = None) -> ImportBatchResult:
        """Stage header-derived people followed by interaction candidates."""
        source = f"import/{source_type}"
        extracted = self._extractor.extract(
            source_type,
            content=content,
            path=path,
            self_addresses=self._self_addresses(),
        )
        if not extracted.people and not extracted.interactions:
            raise ImportPipelineError("no_candidates", "source contains no external import candidates")
        batch_id = new_id()
        now = self._clock.now()
        person_rows: list[StagedImportRow] = []
        email_to_candidate_id: dict[str, str] = {}
        for candidate in extracted.people:
            candidate_id = new_id()
            email_to_candidate_id[candidate.email] = candidate_id
            matched = self._match_existing(candidate.email, candidate.name)
            aliases = [{"value": candidate.email, "kind": AliasKind.HANDLE.value}]
            aliases.extend({"value": name, "kind": AliasKind.OTHER.value} for name in candidate.alternate_names)
            person_rows.append(
                StagedImportRow(
                    id=candidate_id,
                    batch_id=batch_id,
                    source=source,
                    candidate={
                        "type": "person",
                        "name": candidate.name,
                        "aliases": aliases,
                        "matched_person_id": matched.id if matched else None,
                        "message_id": candidate.message_id,
                        "date": candidate.date.isoformat() if candidate.date else None,
                    },
                    status="pending",
                    created_at=now,
                )
            )
        interaction_rows = [
            StagedImportRow(
                id=new_id(),
                batch_id=batch_id,
                source=source,
                candidate={
                    "type": "interaction",
                    "summary": candidate.subject or "Email correspondence",
                    "participant_candidate_ids": [
                        email_to_candidate_id[email] for email in candidate.participant_emails
                    ],
                    "channel": "email",
                    "message_id": candidate.message_id,
                    "date": candidate.occurred_at.isoformat(),
                },
                status="pending",
                created_at=now,
            )
            for candidate in extracted.interactions
        ]
        rows = [*person_rows, *interaction_rows]
        self._staging.stage_batch(rows)
        return ImportBatchResult(
            batch_id=batch_id,
            candidate_count=len(rows),
            skipped_message_ids=extracted.skipped_message_ids,
        )

    def _match_existing(self, email: str, name: str) -> Person | None:
        for value in (email, name):
            matches = self._people.find_by_normalized_name(normalize_name(value))
            if len(matches) == 1:
                return matches[0]
        return None

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
    ) -> None:
        self._people = people
        self._staging = staging
        self._remember_person = remember_person
        self._record_interaction = record_interaction

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
                source=row.source,
                session=candidate.get("message_id"),
            )
        )
        return result.person.id
