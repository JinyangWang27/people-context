"""Source import orchestration, review, and selective commit."""

from __future__ import annotations

from typing import Any

from people_context.app._mutation import transactional, unit_of_work_for
from people_context.app.imports.identity import (
    MatchDisposition,
    candidate_identity_tokens,
    match_person_candidate,
)
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
from people_context.app.records.observations import RecordObservation, RecordObservationInput
from people_context.app.records.traits import RecordTrait, RecordTraitInput
from people_context.app.relationships.commands import SetRelationship, SetRelationshipInput
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock
from people_context.ports.imports import ImportExtractor, ImportStagingStore, StagedImportRow
from people_context.ports.repository import PersonReader

#: Candidate types that commit against exactly one batch-local person.
_PERSON_SCOPED_TYPES = ("affiliation", "fact", "observation", "trait")


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
            max_candidates=limits.max_candidates,
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
    """Commit accepted people first, then every accepted candidate whose people resolved.

    Each candidate type is written through the use case that already owns it, so an imported
    record is indistinguishable from a directly recorded one: the same validation, the same
    provenance, and the same audit and changelog seam. Import has no privileged write path.
    """

    def __init__(
        self,
        people: PersonReader,
        staging: ImportStagingStore,
        remember_person: RememberPerson,
        record_interaction: RecordInteraction,
        set_affiliation: SetAffiliation,
        record_fact: RecordFact,
        record_observation: RecordObservation,
        record_trait: RecordTrait,
        set_relationship: SetRelationship,
    ) -> None:
        self._people = people
        self._staging = staging
        self._remember_person = remember_person
        self._record_interaction = record_interaction
        self._set_affiliation = set_affiliation
        self._record_fact = record_fact
        self._record_observation = record_observation
        self._record_trait = record_trait
        self._set_relationship = set_relationship
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
        unresolved: list[str] = []
        skipped = [row.id for row in rows if row.id in accepted and row.status == "committed"]
        resolution = self._existing_resolution(rows)
        for row in rows:
            if row.id not in accepted or row.status == "committed" or row.candidate.get("type") != "person":
                continue
            person_id = self._commit_person(row)
            if person_id is None:
                unresolved.append(row.id)
                continue
            resolution[row.id] = person_id
            committed.append(row.id)
        for row in rows:
            if row.id not in accepted or row.status == "committed":
                continue
            candidate_type = row.candidate.get("type")
            if candidate_type not in _PERSON_SCOPED_TYPES:
                continue
            person_candidate_id = row.candidate["person_candidate_id"]
            person_id = resolution.get(person_candidate_id)
            if person_id is None:
                unresolved.append(row.id)
                continue
            self._commit_person_scoped(str(candidate_type), person_id, row)
            committed.append(row.id)
        for row in rows:
            if row.id not in accepted or row.status == "committed" or row.candidate.get("type") != "relationship":
                continue
            subject_id = resolution.get(row.candidate["from_candidate_id"])
            object_id = resolution.get(row.candidate["to_candidate_id"])
            # Staging refuses a candidate whose two refs are the same string, but two distinct
            # refs can still resolve to one person — agents are told to stage a candidate for
            # every participant, so a name and a matching handle routinely describe the same
            # existing identity. Committing that would write the self-loop `merge_people` has
            # to clean up, so the edge stays unresolved and a corrected batch can be re-staged.
            if subject_id is None or object_id is None or subject_id == object_id:
                unresolved.append(row.id)
                continue
            self._set_relationship.execute(
                SetRelationshipInput(
                    subject_id=subject_id,
                    object_id=object_id,
                    type=row.candidate["relationship_type"],
                    confidence=row.candidate.get("confidence"),
                    source=row.source,
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

    def _commit_person_scoped(self, candidate_type: str, person_id: str, row: StagedImportRow) -> None:
        """Write one resolved person-scoped candidate through its own audited use case."""
        candidate = row.candidate
        if candidate_type == "affiliation":
            self._set_affiliation.execute(
                SetAffiliationInput(
                    person_id=person_id,
                    org=candidate["org"],
                    role=candidate["role"],
                    valid_from=candidate.get("valid_from"),
                    valid_to=candidate.get("valid_to"),
                    confidence=candidate.get("confidence"),
                    source=row.source,
                    session=candidate.get("message_id"),
                )
            )
        elif candidate_type == "fact":
            self._record_fact.execute(
                RecordFactInput(
                    person_id=person_id,
                    predicate=candidate["predicate"],
                    value=candidate["value"],
                    valid_from=candidate.get("valid_from"),
                    valid_to=candidate.get("valid_to"),
                    confidence=candidate.get("confidence"),
                    sensitivity=candidate.get("sensitivity", "personal"),
                    source=row.source,
                    session=candidate.get("message_id"),
                )
            )
        elif candidate_type == "observation":
            # `observed_at` is absent when the source established no event time; the released
            # `RecordObservation` clock behavior is what fills it in, not a guess made here.
            self._record_observation.execute(
                RecordObservationInput(
                    person_id=person_id,
                    text=candidate["text"],
                    observed_at=candidate.get("observed_at"),
                    sensitivity=candidate.get("sensitivity", "personal"),
                    source=row.source,
                )
            )
        else:
            self._record_trait.execute(
                RecordTraitInput(
                    person_id=person_id,
                    category=candidate["category"],
                    value=candidate["value"],
                    evidence_note=candidate["evidence_note"],
                    confidence=candidate["confidence"],
                    sensitivity=candidate.get("sensitivity", "personal"),
                    source=row.source,
                )
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
                resolved = self._rematch(row.candidate)
                if resolved is not None:
                    resolution[row.id] = resolved
        return resolution

    def _rematch(self, candidate: dict[str, Any]) -> str | None:
        """Re-derive one already-committed person candidate's identity from stored names.

        A row staged by an extraction batch is re-derived with the matcher that staged it, so
        the answer cannot change merely because it is being asked at commit time; anything
        older keeps the released first-unique-token behavior it was staged under.
        """
        aliases = list(candidate.get("aliases", []))
        name = str(candidate["name"])
        if candidate.get("match_disposition") is not None:
            match = match_person_candidate(self._people, candidate_identity_tokens(name, aliases))
            return match.person_id
        values = [str(alias["value"]) for alias in aliases]
        values.append(name)
        for value in values:
            matches = self._people.find_by_normalized_name(normalize_name(value))
            if len(matches) == 1:
                return matches[0].id
        return None

    def _commit_person(self, row: StagedImportRow) -> str | None:
        """Commit one accepted person candidate, or report that its identity is still open.

        An ambiguous candidate is the one case that can decline to commit. It never falls
        through to `RememberPerson`: "several people this could be" is not evidence of a new
        person, and creating one would durably invent the duplicate the ambiguity warned about.
        Re-running the same deterministic match is what can resolve it later — after a merge or
        a correction leaves exactly one active person — and until then the candidate and every
        accepted dependant that needs it stay unresolved, ready for a later commit.
        """
        candidate = row.candidate
        matched_id = candidate.get("matched_person_id")
        if candidate.get("match_disposition") == MatchDisposition.AMBIGUOUS.value:
            tokens = candidate_identity_tokens(candidate["name"], candidate.get("aliases", []))
            match = match_person_candidate(self._people, tokens)
            if match.disposition is not MatchDisposition.MATCHED:
                return None
            matched_id = match.person_id
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
