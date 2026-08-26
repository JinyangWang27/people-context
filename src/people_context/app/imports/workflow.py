"""Source import orchestration, review, and selective commit."""

from __future__ import annotations

from people_context.app._mutation import transactional, unit_of_work_for
from people_context.app.imports.limits import UNBOUNDED_IMPORT_BUDGET, ImportBudget
from people_context.app.imports.models import (
    CommitImportResult,
    ImportBatchResult,
    ImportPipelineError,
    ImportReviewResult,
    ImportReviewRow,
)
from people_context.app.imports.staging import CandidateStager
from people_context.app.people.remember import AliasInput, RememberPerson, RememberPersonInput
from people_context.app.records.affiliations import SetAffiliation, SetAffiliationInput
from people_context.app.records.facts import RecordFact, RecordFactInput
from people_context.app.records.interactions import RecordInteraction, RecordInteractionInput
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock
from people_context.ports.imports import ImportExtractor, ImportStagingStore, StagedImportRow
from people_context.ports.repository import PersonReader


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

    def execute(
        self,
        source_type: str,
        content: str | None = None,
        path: str | None = None,
        self_sender: str | None = None,
        budget: ImportBudget | None = None,
    ) -> ImportBatchResult:
        """Stage header-derived people followed by interaction candidates.

        ``self_sender`` is an optional explicit label — such as ``You`` or a bare phone number —
        for sources that identify the user by display label rather than by address.

        ``budget`` is the calling boundary's resource ceiling and defaults to the released
        unbounded contract. When one is supplied it bounds the source read and the staged
        batch alike, so an over-budget import fails before it reaches durable staging.
        """
        limits = budget or UNBOUNDED_IMPORT_BUDGET
        source = f"import/{source_type}"
        self_addresses, self_names = self._self_identity()
        extracted = self._extractor.extract(
            source_type,
            content=content,
            path=path,
            self_addresses=self_addresses,
            self_names=self_names,
            self_sender=self_sender,
            max_source_bytes=limits.max_source_bytes,
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
                aliases.extend({"value": name, "kind": AliasKind.OTHER.value} for name in candidate.alternate_names)
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
                    "summary": "Email correspondence",
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
            budget=limits,
        )

    def _self_identity(self) -> tuple[set[str], set[str]]:
        """Return the self person's handle aliases and normalized canonical name plus alias values."""
        person = self._people.get_self()
        if person is None:
            return set(), set()
        addresses = {alias.value for alias in person.aliases if alias.kind == AliasKind.HANDLE}
        values = {person.canonical_name, *(alias.value for alias in person.aliases)}
        names = {normalized for value in values if (normalized := normalize_name(value))}
        return addresses, names


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
        self._uow = unit_of_work_for(staging)

    @transactional
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
