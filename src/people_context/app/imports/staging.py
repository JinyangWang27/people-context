"""Candidate validation, reference rewriting, matching, and atomic staging."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from people_context.app._mutation import audit_mutation, unit_of_work_for
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
from people_context.app.imports.sources import (
    build_source_claim,
    require_source_kind_for,
    source_previously_redacted_error,
    source_session_snapshot,
)
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import new_id, normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.imports import ImportStagingStore, StagedImportRow
from people_context.ports.repository import PersonReader
from people_context.ports.sources import (
    STATUS_REDACTED,
    ImportSourceStore,
    SourceClaimOutcome,
    SourceSessionClaim,
    SourceSessionRow,
)

_CANDIDATES_ADAPTER = TypeAdapter(list[CandidateInput])


class CandidateStager:
    """Validate, match, rewrite references, and atomically stage one candidate batch."""

    def __init__(
        self,
        people: PersonReader,
        staging: ImportStagingStore,
        clock: Clock,
        sources: ImportSourceStore | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._people = people
        self._staging = staging
        self._clock = clock
        self._sources = sources
        self._audit = audit
        if sources is not None and audit is None:
            # A receipt is replicable primary state, so writing one without journalling it would
            # leave durable provenance outside the accountability record. That is always a wiring
            # mistake rather than a configuration, so it is reported loudly here.
            raise RuntimeError("a source-tracked candidate stager requires an audit log")
        # The source store's boundary reserves the write lock, because claiming a source means
        # reading a uniqueness claim and then acting on what was read.
        self._uow = unit_of_work_for(sources, staging, audit)

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
        claim: SourceSessionClaim | None = None,
    ) -> ImportBatchResult:
        """Stage one batch, refusing an over-budget one before any row is persisted.

        ``budget`` defaults to the released unbounded contract. When a caller supplies one the
        count is checked before the batch is validated and the payload is measured while rows
        are built, so an over-budget source stops the work instead of being discovered after it.

        ``strict_identity`` selects the ambiguity-preserving matcher. It is off by default so
        that every caller that predates M17 — the source importers and `pctx init` among them —
        keeps staging the person rows it always staged.

        ``claim`` opts this batch into M18 source tracking. Claim, receipt, its journal entry, and
        every candidate row are then published in one write-reserving transaction, so a canonical
        claim can never be visible without the batch it promises or vice versa. Validation and row
        building happen before that reservation is taken: the race the reservation exists to close
        is between two publications, not between two callers parsing their own input. A batch
        staged without a claim behaves exactly as it did before source sessions existed.
        """
        limits = budget or UNBOUNDED_IMPORT_BUDGET
        tracked = claim is not None and self._sources is not None
        self._reject_excess_candidates(len(candidates), limits)
        validated = self._validate(candidates)
        self._require_tracking_for_evidence(validated, tracked)
        batch_id = new_id()
        references = _batch_references(validated)
        rows = self._rows(batch_id, source, validated, references, limits, strict_identity)
        result = ImportBatchResult(
            batch_id=batch_id,
            candidate_count=len(rows),
            skipped_message_ids=skipped_message_ids or [],
            skipped_without_id=skipped_without_id,
            skipped_cards=skipped_cards or [],
        )
        if claim is None or self._sources is None:
            self._staging.stage_batch(rows)
            return result
        with self._uow:
            outcome = self._sources.claim_and_stage(
                claim,
                rows,
                session_id=new_id(),
                batch_id=batch_id,
                created_at=self._clock.now(),
            )
            if not outcome.created:
                # The claim was already owned, so nothing was written. Whether this reports the
                # existing batch or refuses a terminal one, the reservation closes empty.
                return self._duplicate_result(result, outcome)
            self._audit_session(outcome.session)
        return result.model_copy(update={"source_session_id": outcome.session.id})

    def _audit_session(self, session: SourceSessionRow) -> None:
        """Journal one new receipt through the ordinary mutation seam.

        A receipt is replicable primary state, not operational staging, so it is accountable like
        any other durable write. The payload carries the caller-authored label and external id
        because that is what a faithful replay needs — and it is exactly what hard forget later
        has to scrub from this history when it touches this source.

        The replay image is the whole row, as it is for every other primary write here. The two
        fields the accountability payload leaves out are the two a consumer could not put back:
        `created_at` is required by the schema, and `claim_key` cannot be re-derived, because a
        forced session deliberately carries a digest and no key at all.
        """
        if self._audit is None:
            return
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="import_source_session",
            entity_id=session.id,
            payload={
                "source_kind": session.source_kind,
                "label": session.label,
                "external_source_id": session.external_source_id,
                "content_digest": session.content_digest,
                "extraction_fingerprint": session.extraction_fingerprint,
                "extraction_contract_revision": session.extraction_contract_revision,
                "batch_id": session.batch_id,
                "status": session.status,
            },
            replay_payload=source_session_snapshot(session),
            changed_fields=["batch_id", "source_kind", "status"],
            source="import",
        )

    @staticmethod
    def _duplicate_result(result: ImportBatchResult, outcome: SourceClaimOutcome) -> ImportBatchResult:
        """Report the batch the winning claim already owns, having staged nothing.

        A claim that resolves to a terminal redacted receipt deliberately has no batch to report:
        hard forget removed it. Fabricating or reusing one would hand back an id that reviews and
        commits nothing, so this refuses with a stable code instead.
        """
        session = outcome.session
        if session.status == STATUS_REDACTED or session.batch_id is None:
            raise source_previously_redacted_error(session.id)
        return result.model_copy(
            update={
                "batch_id": session.batch_id,
                "candidate_count": outcome.candidate_count,
                "source_session_id": session.id,
                "duplicate": True,
                "reviewable": outcome.reviewable,
            }
        )

    @staticmethod
    def _require_tracking_for_evidence(candidates: list[CandidateInput], tracked: bool) -> None:
        """Refuse a same-batch evidence citation this batch could never resolve.

        A batch-local citation is resolved at commit through the M18.1 candidate commit mapping —
        that mapping is the mechanism, and it exists only for a source-tracked batch. Without one,
        a caller who commits the evidence and its trait in separate invocations strands the trait:
        the evidence row is already committed so it is skipped, no mapping records what it
        produced, and the citation can never be answered. Re-staging is the only way out, and
        nothing would have said so.

        Refusing here makes the dependency explicit while the batch can still be declined whole,
        and the remedy is one field: name the material with `source_kind`. Durable `evidence_ids`
        need no receipt and stay available either way, because they already name the record.
        """
        if tracked:
            return
        if not any(
            isinstance(candidate, TraitCandidateInput) and candidate.evidence_refs
            for candidate in candidates
        ):
            return
        raise ImportPipelineError(
            "evidence_requires_source_tracking",
            "citing evidence staged in the same batch requires a source-tracked batch; "
            "supply source_kind, or cite the durable records directly with evidence_ids",
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
        references: _BatchReferences,
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
        for index, candidate in enumerate(candidates):
            row = self._row(batch_id, source, index, candidate, references, strict_identity)
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
        _check_evidence_references(validated)
        return validated

    def _row(
        self,
        batch_id: str,
        source: str,
        index: int,
        candidate: CandidateInput,
        references: _BatchReferences,
        strict_identity: bool,
    ) -> StagedImportRow:
        staged = candidate.model_dump(mode="json", exclude_none=True)
        row_id = references.row_ids[index]
        if isinstance(candidate, PersonCandidateInput):
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
        elif isinstance(candidate, InteractionCandidateInput):
            staged.pop("participant_refs")
            staged.pop("evidence_ref", None)
            staged["participant_candidate_ids"] = [references.people[ref] for ref in candidate.participant_refs]
        elif isinstance(candidate, RelationshipCandidateInput):
            staged.pop("from_ref")
            staged.pop("to_ref")
            staged["from_candidate_id"] = references.people[candidate.from_ref]
            staged["to_candidate_id"] = references.people[candidate.to_ref]
        else:
            staged.pop("person_ref")
            staged.pop("evidence_ref", None)
            staged["person_candidate_id"] = references.people[candidate.person_ref]
            if isinstance(candidate, TraitCandidateInput):
                _rewrite_trait_evidence(staged, candidate, references)
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

    def execute(
        self,
        source: str,
        candidates: list[dict[str, Any]],
        *,
        strict_identity: bool = False,
        source_kind: str | None = None,
        content_digest: str | None = None,
        extraction_fingerprint: str | None = None,
        label: str | None = None,
        external_source_id: str | None = None,
    ) -> ImportBatchResult:
        """Stage one agent request, bounding it first when it opts into an M17 candidate type.

        The extraction bounds and the ambiguity-preserving matcher are selected by the same
        condition, and it is read off the raw request rather than the validated one: a request
        that names an M17 type is an extraction request whether or not it turns out to be
        well-formed, and both decisions must be made before anything is parsed or staged.

        `strict_identity` lets a caller demand the ambiguity-preserving matcher for every batch
        regardless of its candidate types. The condition above exists to leave the released MCP
        contract exactly as it shipped; a boundary introduced after M17 has no such history, and
        every batch reaching it is agent-extracted whether or not it happens to use an M17 type.
        Ambiguity there is a property of the *identities*, not of the candidate vocabulary: a
        person plus a fact, distilled from a transcript, can name someone two existing people
        could equally be, and resolving that to one of them would attach the fact to a guess.

        `source_kind` opts the request into an M18 receipt. A caller that also supplies a
        `content_digest` it computed over the source artifact gets a canonical duplicate claim;
        one that does not gets a session that deliberately asserts none, because People Context
        was never given bytes it could recognize the source by and must not imply otherwise. An
        `extraction_fingerprint` stays optional either way: People Context did not perform this
        extraction and will not invent a description of the caller's configuration.

        People Context never hashes text it was not given. A supplied digest is provenance
        metadata from the caller, not an independent verification of source bytes.
        """
        normalized_source = source.strip()
        if not normalized_source:
            raise _invalid_candidates("source must not be blank")
        extraction = contains_extraction_candidate(candidates)
        if extraction:
            enforce_extraction_request_limits(normalized_source, candidates)
        if source_kind is None:
            # Receipt metadata without a kind to record it would be accepted and then dropped,
            # so a caller who asked for duplicate protection by supplying a digest would be told
            # the staging succeeded while quietly not getting it.
            require_source_kind_for(
                content_digest=content_digest,
                extraction_fingerprint=extraction_fingerprint,
                label=label,
                external_source_id=external_source_id,
            )
        claim = (
            None
            if source_kind is None
            else build_source_claim(
                source_kind=source_kind,
                content_digest=content_digest,
                extraction_fingerprint=extraction_fingerprint,
                label=label,
                external_source_id=external_source_id,
            )
        )
        return self._stager.execute(
            f"import/agent:{normalized_source}",
            candidates,
            strict_identity=strict_identity or extraction,
            claim=claim,
        )


def _check_evidence_references(candidates: list[CandidateInput]) -> None:
    """Refuse a batch whose evidence references cannot be rewritten deterministically.

    Both failures are refused before staging rather than resolved leniently at commit. A repeated
    `evidence_ref` has no single meaning — two records answer to one label — and a trait citing a
    label nothing declares has no meaning at all. Accepting either would produce a batch whose
    traits look grounded in review and then commit ungrounded, or grounded in whichever candidate
    the rewrite happened to reach last.

    The messages name the caller's own label, which is a value the caller authored and already
    holds. Nothing here reports a candidate's content.
    """
    declared: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, (ObservationCandidateInput, InteractionCandidateInput)):
            continue
        if candidate.evidence_ref is None:
            continue
        if candidate.evidence_ref in declared:
            raise _invalid_candidates(
                "duplicate evidence reference",
                details=[
                    {
                        "type": "value_error",
                        "loc": [index, "evidence_ref"],
                        "msg": f"duplicate evidence ref: {candidate.evidence_ref}",
                    }
                ],
            )
        declared[candidate.evidence_ref] = index
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, TraitCandidateInput):
            continue
        unknown = sorted(set(candidate.evidence_refs) - declared.keys())
        if unknown:
            raise _invalid_candidates(
                "unknown evidence reference",
                details=[
                    {
                        "type": "value_error",
                        "loc": [index, "evidence_refs"],
                        "msg": (
                            "evidence_refs must name an observation or interaction candidate in this batch: "
                            f"{', '.join(unknown)}"
                        ),
                    }
                ],
            )


@dataclass(frozen=True)
class _BatchReferences:
    """The two caller-facing namespaces of one batch, resolved to canonical candidate ids.

    They are separate namespaces on purpose. A person `ref` and an `evidence_ref` answer
    different questions — who is this about, and what is this drawn from — and commit resolves
    them through different maps. Merging them would let a trait cite a person, which is not a
    thing a trait can rest on.

    ``row_ids`` is every candidate's id, allocated up front and indexed by position, because an
    evidence reference has to be rewritten to a row id that does not exist until it is minted.
    """

    people: dict[str, str]
    evidence: dict[str, str]
    row_ids: list[str]


def _batch_references(candidates: list[CandidateInput]) -> _BatchReferences:
    """Allocate one canonical id per candidate and index both reference namespaces onto them."""
    row_ids = [new_id() for _ in candidates]
    people = {
        candidate.ref: row_ids[index]
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, PersonCandidateInput)
    }
    evidence = {
        candidate.evidence_ref: row_ids[index]
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, (ObservationCandidateInput, InteractionCandidateInput))
        and candidate.evidence_ref is not None
    }
    return _BatchReferences(people=people, evidence=evidence, row_ids=row_ids)


def _rewrite_trait_evidence(
    staged: dict[str, Any],
    candidate: TraitCandidateInput,
    references: _BatchReferences,
) -> None:
    """Replace a trait's caller-local evidence labels with canonical candidate ids.

    This mirrors the person-ref rewrite exactly: the caller's own labels never reach storage, so
    review and commit read canonical ids and nothing has to interpret an agent's naming scheme.
    Both collections are written only when they hold something, which is what keeps a trait that
    cites nothing byte-identical to one staged before evidence links existed.
    """
    staged.pop("evidence_refs", None)
    staged.pop("evidence_ids", None)
    if candidate.evidence_refs:
        staged["evidence_candidate_ids"] = [references.evidence[ref] for ref in candidate.evidence_refs]
    if candidate.evidence_ids:
        staged["evidence_ids"] = list(candidate.evidence_ids)


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
