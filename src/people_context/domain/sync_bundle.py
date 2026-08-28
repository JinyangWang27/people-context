"""Strict, versioned bootstrap sync-bundle contracts.

These models are the restore contract, not a loose ``dict[str, Any]`` envelope. Every
model forbids unknown fields, no field carries a silent default, timestamps must be
timezone-aware and are normalized to UTC, and identifiers must be non-blank. Structural
validation therefore fails closed before any consumer inspects the document.

Structural parsing alone cannot prove a document is internally consistent, so
:func:`validate_bundle_document` adds the document-level cross-field rules. It runs on the
restore path only: export builds its document from one consistent database snapshot, and
making the shared model reject anything a live database can legitimately hold would turn a
readable store into an unexportable one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from people_context.domain.import_provenance import (
    REVIEWABLE_SESSION_STATUSES,
    check_contract_revision,
    check_hex64,
    check_opaque_label,
    check_source_kind,
    check_staged_candidate,
    compose_claim_key,
    staged_candidate_references,
)
from people_context.domain.person import AliasKind
from people_context.domain.relationship_vocabulary import SEEDED_RELATIONSHIP_TYPES
from people_context.domain.reminder import ReminderKind, ReminderStatus
from people_context.domain.shared import Confidence, Sensitivity
from people_context.domain.trait import TraitCategory

SYNC_BUNDLE_FORMAT = "people-context-sync-bundle"

#: The version this release emits. M18.1 added durable source receipts, candidate commit
#: mappings, and the staging rows an incomplete batch needs, and the bundle is deliberately not
#: additively extensible within a version — a reader that accepts a field must understand it —
#: so carrying that state required a new version rather than optional fields on version 1.
SYNC_BUNDLE_VERSION = 2

#: Versions restore accepts. A released version stays readable; only emission moves forward.
SUPPORTED_SYNC_BUNDLE_VERSIONS = (1, 2)

#: Upper bound on reported reasons. A hostile or badly corrupted document must not turn one
#: refusal into an unbounded message; the count of suppressed reasons is reported instead.
MAX_REPORTED_DETAILS = 20


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("identifier must not be blank")
    return value


def _reject_numeric_timestamp(value: Any) -> Any:
    """Refuse epoch numbers, whose seconds-or-milliseconds meaning is guessed, not declared."""
    if isinstance(value, (int, float)):
        raise ValueError("timestamp must be an ISO-8601 string carrying a UTC offset")
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


#: SQLite persists ``INTEGER`` as a signed 64-bit value. A larger number parses happily as a
#: Python int but raises ``OverflowError`` when bound mid-restore, which would escape the
#: structured-refusal contract as a traceback after the user had already been prompted.
SQLITE_MAX_INTEGER = 2**63 - 1

Identifier = Annotated[str, AfterValidator(_require_non_blank)]
#: Receipt metadata is held to the rules the staging boundary applies, so a restored receipt is
#: exactly as bounded as one this installation created.
BundleOpaqueLabel = Annotated[str, AfterValidator(check_opaque_label)]
BundleDigest = Annotated[str, AfterValidator(check_hex64)]
BundleContractRevision = Annotated[str, AfterValidator(check_contract_revision)]
UtcDatetime = Annotated[datetime, BeforeValidator(_reject_numeric_timestamp), AfterValidator(_require_utc)]
StoredInteger = Annotated[int, Field(ge=0, le=SQLITE_MAX_INTEGER)]
#: One below the storable maximum: restore advances the local clock past the bundle watermark
#: through ``observe()``, which adds one to a logical counter, and that result must still fit.
HlcComponent = Annotated[int, Field(ge=0, le=SQLITE_MAX_INTEGER - 1)]


class StrictBundleModel(BaseModel):
    """Base model rejecting unknown fields anywhere in the bundle document."""

    model_config = ConfigDict(extra="forbid")


class BundleProvenance(StrictBundleModel):
    """Verbatim provenance of one asserted row."""

    source: str
    session: str | None
    stated_by: str | None


class BundleValidityPeriod(StrictBundleModel):
    """Verbatim validity window of one time-aware row."""

    valid_from: date | None
    valid_to: date | None

    @model_validator(mode="after")
    def _check_order(self) -> BundleValidityPeriod:
        """Mirror the domain ``ValidityPeriod`` invariant.

        Ordinary reads rehydrate stored rows through the domain model, so committing an
        inverted window would leave a database whose own ``export``, ``show``, and context
        reads raise. Restore must refuse it before the preview instead.
        """
        if self.valid_from is not None and self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValueError("valid_from must be <= valid_to")
        return self


class BundleAlias(StrictBundleModel):
    """One alternate name carried inside a bundled person row."""

    id: Identifier
    value: str
    kind: AliasKind
    lang: str | None
    script: str | None


class BundlePerson(StrictBundleModel):
    """One bundled person row, including its nested aliases."""

    id: Identifier
    canonical_name: str
    is_self: bool
    summary: str | None
    aliases: list[BundleAlias]
    created_at: UtcDatetime
    updated_at: UtcDatetime
    deleted_at: UtcDatetime | None


class BundleOrganization(StrictBundleModel):
    """One bundled organization row."""

    id: Identifier
    name: str
    kind: str | None


class BundleAffiliation(StrictBundleModel):
    """One bundled person-to-organization affiliation row."""

    id: Identifier
    person_id: Identifier
    org_id: Identifier
    role: str
    period: BundleValidityPeriod
    confidence: Confidence
    provenance: BundleProvenance
    created_at: UtcDatetime


class BundleRelationship(StrictBundleModel):
    """One bundled directed relationship row."""

    id: Identifier
    subject_id: Identifier
    object_id: Identifier
    type: str
    label: str | None
    period: BundleValidityPeriod
    confidence: Confidence
    provenance: BundleProvenance
    created_at: UtcDatetime


class BundleFact(StrictBundleModel):
    """One bundled factual assertion row."""

    id: Identifier
    person_id: Identifier
    predicate: str
    value: str
    period: BundleValidityPeriod
    recorded_at: UtcDatetime
    confidence: Confidence
    sensitivity: Sensitivity
    provenance: BundleProvenance


class BundleObservation(StrictBundleModel):
    """One bundled subjective observation row."""

    id: Identifier
    person_id: Identifier
    text: str
    observed_at: UtcDatetime
    sensitivity: Sensitivity
    provenance: BundleProvenance


class BundleTrait(StrictBundleModel):
    """One bundled derived trait row."""

    id: Identifier
    person_id: Identifier
    category: TraitCategory
    value: str
    evidence_note: str | None
    confidence: Confidence
    sensitivity: Sensitivity
    provenance: BundleProvenance
    updated_at: UtcDatetime


class BundleInteraction(StrictBundleModel):
    """One bundled interaction summary row and its participants."""

    id: Identifier
    summary: str
    occurred_at: UtcDatetime
    channel: str | None
    participant_ids: list[Identifier]
    sensitivity: Sensitivity
    provenance: BundleProvenance


class BundleReminder(StrictBundleModel):
    """One bundled reminder row."""

    id: Identifier
    person_id: Identifier
    text: str
    kind: ReminderKind
    due_at: UtcDatetime | None
    recurrence: str | None
    status: ReminderStatus
    created_at: UtcDatetime


class BundleUserPreference(StrictBundleModel):
    """One bundled user preference row with its opaque JSON value."""

    key: Identifier
    value: Any
    updated_at: UtcDatetime


class BundleAuditEntry(StrictBundleModel):
    """One bundled accountability audit row, carried verbatim after redaction."""

    id: Identifier
    ts: UtcDatetime
    op: str
    entity_type: str
    entity_id: Identifier
    payload: dict[str, Any]
    source: str


class BundleSnapshot(StrictBundleModel):
    """The complete portable domain snapshot in the established export row shape."""

    people: list[BundlePerson]
    organizations: list[BundleOrganization]
    affiliations: list[BundleAffiliation]
    relationships: list[BundleRelationship]
    facts: list[BundleFact]
    observations: list[BundleObservation]
    traits: list[BundleTrait]
    interactions: list[BundleInteraction]
    reminders: list[BundleReminder]
    user_preferences: list[BundleUserPreference]
    audit_log: list[BundleAuditEntry]


class BundleRelationshipType(StrictBundleModel):
    """One bundled ``relationship_types`` row, seeded or custom."""

    type: Identifier
    inverse: str | None
    symmetric: bool
    category: str
    canonical: bool

    @model_validator(mode="after")
    def _validate_direction(self) -> BundleRelationshipType:
        """Mirror the domain ``RelationshipType`` invariant.

        The vocabulary store rehydrates these rows, so an impossible direction would break
        ``relationship-types`` and every graph read on the restored database.
        """
        if self.symmetric and self.inverse is not None:
            raise ValueError("symmetric relationship types cannot define an inverse")
        if not self.canonical and self.inverse is None:
            raise ValueError("non-canonical relationship types must name their canonical inverse")
        return self


class BundleRelationshipSynonym(StrictBundleModel):
    """One bundled ``relationship_type_synonyms`` row."""

    synonym: Identifier
    type: Identifier


class BundleRelationshipVocabulary(StrictBundleModel):
    """Both relationship-vocabulary tables, including custom rows."""

    types: list[BundleRelationshipType]
    synonyms: list[BundleRelationshipSynonym]


class BundleWatermark(StrictBundleModel):
    """The origin device's hybrid logical clock at snapshot time."""

    hlc_physical_ms: HlcComponent
    hlc_logical: HlcComponent


class BundleDevice(StrictBundleModel):
    """One bundled device row with its persisted hybrid logical clock."""

    id: Identifier
    display_name: str | None
    public_key: str | None
    created_at: UtcDatetime
    retired_at: UtcDatetime | None
    hlc_physical_ms: HlcComponent
    hlc_logical: HlcComponent


class BundleChangelogEntry(StrictBundleModel):
    """One bundled replayable changelog row, carried verbatim."""

    op_id: Identifier
    device_id: Identifier
    hlc_physical_ms: HlcComponent
    hlc_logical: HlcComponent
    transaction_id: Identifier
    entity_type: str
    entity_id: Identifier
    op_kind: str
    payload: dict[str, Any]
    changed_fields: list[str]
    actor: dict[str, Any]
    schema_version: StoredInteger
    inserted_at: UtcDatetime


class BundleSourceSession(StrictBundleModel):
    """One bundled import receipt.

    Every field is held to the same rule the staging boundary applies, because a receipt restored
    from a bundle is as durable as one this installation created. ``source_kind`` matters most:
    hard forget deliberately keeps it on a terminal redacted row while scrubbing the caller-authored
    fields around it, and that is only safe while the kind cannot hold a person or a title. A
    restore that accepted ``Interview with Alice`` as a kind would let erased wording survive — and
    be re-exported — through the one field erasure is designed to preserve.

    A terminal ``redacted`` receipt is what remains after hard forget emptied a source: it must
    still be claim-backed, and it must carry none of the caller-authored or optional inspection
    state that erasure cleared. Enforcing that here means a hand-edited bundle cannot smuggle a
    scrubbed label back into a restored database.
    """

    id: Identifier
    source_kind: Annotated[str, AfterValidator(check_source_kind)]
    label: BundleOpaqueLabel | None
    external_source_id: BundleOpaqueLabel | None
    content_digest: BundleDigest | None
    extraction_fingerprint: BundleDigest | None
    extraction_contract_revision: BundleContractRevision | None
    claim_key: str | None
    batch_id: str | None
    status: Literal["staged", "partially_committed", "committed", "redacted"]
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _check_claim(self) -> BundleSourceSession:
        if self.claim_key is not None and self.content_digest is None:
            raise ValueError("a canonical claim requires a content_digest")
        if self.claim_key is not None and self.claim_key != compose_claim_key(
            self.source_kind, self.content_digest, self.extraction_fingerprint
        ):
            # Duplicate detection looks a source up by the key composed from these very fields.
            # A key that does not match them would either miss its own receipt and stage the
            # source twice, or occupy the key of an unrelated source and suppress that import.
            raise ValueError("claim_key must be the canonical composition of the receipt's own fields")
        if self.status != "redacted":
            return self
        if self.claim_key is None:
            # A terminal receipt exists for exactly one reason: to make its claim non-restageable.
            # Duplicate detection finds it by that key, so a redacted row without one is invisible
            # to the lookup and the forgotten source is staged fresh instead of being refused —
            # which is the whole terminal-forget contract, undone by a null column.
            #
            # This subsumes "must be claim-backed": the first check above already refuses a key
            # without a digest, so requiring the key here leaves no way to reach a digestless
            # redacted receipt. There is deliberately no second digest check to fall through to.
            raise ValueError("a redacted source session must retain its canonical claim key")
        cleared = (self.label, self.external_source_id, self.extraction_contract_revision, self.batch_id)
        if any(value is not None for value in cleared):
            raise ValueError("a redacted source session must carry no cleared caller or batch state")
        return self


class BundleCandidateMapping(StrictBundleModel):
    """One bundled committed-candidate outcome.

    ``merged_away`` is the terminal outcome of a committed relationship candidate whose edge a
    person merge removed as a self-loop. It has no entity id by construction, which is what keeps
    it history rather than a dangling reference.
    """

    candidate_id: Identifier
    batch_id: Identifier
    source_session_id: Identifier
    disposition: Literal["entity", "merged_away"]
    entity_type: str
    entity_id: Identifier | None
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _check_disposition(self) -> BundleCandidateMapping:
        if self.disposition == "entity":
            if self.entity_id is None:
                raise ValueError("an entity mapping must name the durable entity it produced")
            return self
        if self.entity_id is not None:
            raise ValueError("a merged_away mapping must not name an entity")
        if self.entity_type != "relationship":
            raise ValueError("merged_away is only a relationship outcome")
        return self


class BundleStagingRow(StrictBundleModel):
    """One bundled reviewable staging row, carried only for an incomplete batch.

    The candidate is held to the whole persisted shape, not just the parts that make it findable:
    a known type, the canonical reference fields that type requires, every field commit indexes
    directly, and nothing else. A row missing one of those fields does not go unresolved — it
    raises mid-commit, on a batch the restore already accepted. A row carrying a field the stager
    never writes is raw source text that review would display and every later bundle would carry.
    """

    id: Identifier
    batch_id: Identifier
    source: str
    candidate: dict[str, Any]
    status: Literal["pending", "committed"]
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _check_candidate(self) -> BundleStagingRow:
        check_staged_candidate(self.candidate)
        return self


class BundleImportState(StrictBundleModel):
    """Durable import provenance plus the operational staging an incomplete batch needs.

    Mappings are primary state and are carried for **every** exported receipt, including fully
    committed ones whose staging rows were cleaned up: without them a restored database would
    keep the records but lose what produced them. Staging rows are the opposite — operational
    state carried only where a batch still has something to review or commit.
    """

    source_sessions: list[BundleSourceSession]
    candidate_mappings: list[BundleCandidateMapping]
    staging: list[BundleStagingRow]


class SyncBundleDocumentV1(StrictBundleModel):
    """The released version-1 bundle, still accepted by restore and no longer emitted."""

    format: Literal["people-context-sync-bundle"]
    version: Literal[1]
    created_at: UtcDatetime
    origin_device_id: Identifier
    watermark: BundleWatermark
    devices: list[BundleDevice]
    snapshot: BundleSnapshot
    relationship_vocabulary: BundleRelationshipVocabulary
    changelog: list[BundleChangelogEntry]

    def upgraded(self) -> SyncBundleDocument:
        """Return this document in the current in-memory shape, carrying no import state.

        A version-1 bundle genuinely contains no source receipts, so the empty collections are a
        fact about it rather than a default filled in for a missing field. Restoring through one
        shape keeps the writer from branching on version while the strict per-version parsing
        above still refuses a v1 document that carries a v2 field.
        """
        return SyncBundleDocument(
            format=self.format,
            version=2,
            created_at=self.created_at,
            origin_device_id=self.origin_device_id,
            watermark=self.watermark,
            devices=self.devices,
            snapshot=self.snapshot,
            relationship_vocabulary=self.relationship_vocabulary,
            changelog=self.changelog,
            imports=BundleImportState(source_sessions=[], candidate_mappings=[], staging=[]),
        )


class SyncBundleDocument(StrictBundleModel):
    """One complete, point-in-time bootstrap bundle."""

    format: Literal["people-context-sync-bundle"]
    version: Literal[2]
    created_at: UtcDatetime
    origin_device_id: Identifier
    watermark: BundleWatermark
    devices: list[BundleDevice]
    snapshot: BundleSnapshot
    relationship_vocabulary: BundleRelationshipVocabulary
    changelog: list[BundleChangelogEntry]
    imports: BundleImportState


def parse_bundle_payload(payload: Any) -> SyncBundleDocument:
    """Validate one bundle against the shape its own declared version promises.

    Version selects the model before anything is validated, so a version-1 document carrying a
    version-2 collection is refused as an unknown field rather than quietly accepted. Anything
    that does not declare version 1 is held to the current shape, which reports the versions this
    release understands instead of guessing at a newer one.
    """
    declared = payload.get("version") if isinstance(payload, dict) else None
    if declared == 1:
        return SyncBundleDocumentV1.model_validate(payload).upgraded()
    return SyncBundleDocument.model_validate(payload)


class SyncBundleError(Exception):
    """Structured, fail-closed refusal to accept or restore one bundle.

    ``details`` carries stable, machine-readable reasons that name identifiers, table names,
    and counts only. Record contents never appear, so a refusal can be printed or logged
    without disclosing personal data.
    """

    code: ClassVar[str] = "sync_bundle_error"

    def __init__(self, details: Sequence[str]) -> None:
        reported = list(details[:MAX_REPORTED_DETAILS])
        suppressed = len(details) - len(reported)
        if suppressed > 0:
            reported.append(f"and {suppressed} further reason(s)")
        self.details: tuple[str, ...] = tuple(reported)
        super().__init__(f"{self.code}: " + "; ".join(self.details))


class InvalidBundleError(SyncBundleError):
    """The document is not a well-formed, internally consistent version-1 bundle."""

    code = "invalid_bundle"


class TargetNotEmptyError(SyncBundleError):
    """The destination is not the baseline-empty database bootstrap restore requires."""

    code = "target_not_empty"


class RestoreUnavailableError(SyncBundleError):
    """The destination could not be reserved exclusively for the restore transaction."""

    code = "restore_unavailable"


def validate_bundle_document(document: SyncBundleDocument) -> None:
    """Raise :class:`InvalidBundleError` unless every document-level rule holds.

    Reasons from all rules are collected in a stable order so one refusal reports the whole
    picture rather than only the first failure a caller happens to trip over.
    """
    details = [
        *_duplicate_details(document),
        *_origin_details(document),
        *_changelog_device_details(document),
        *_watermark_details(document),
        *_reference_details(document),
        *_import_details(document),
    ]
    if details:
        raise InvalidBundleError(details)


#: Which snapshot collection each candidate mapping entity type must resolve against.
_MAPPED_ENTITY_COLLECTIONS: dict[str, str] = {
    "person": "people",
    "affiliation": "affiliations",
    "fact": "facts",
    "observation": "observations",
    "trait": "traits",
    "interaction": "interactions",
    "relationship": "relationships",
}


def _import_details(document: SyncBundleDocument) -> list[str]:
    """Check that restored import provenance can still answer what it claims to answer.

    A mapping that pointed at a record the bundle does not carry would restore a source whose
    `source show` names an id nothing resolves. A staging row whose batch has no receipt would
    restore a batch that duplicate detection cannot see. A retained person match pointing at an
    inactive person would restore a batch that looks committable and then refuses every record
    depending on it. Each of those is refused here rather than discovered after the restore.
    """
    imports = document.imports
    snapshot = document.snapshot
    sessions = {session.id: session for session in imports.source_sessions}
    batches = {session.batch_id: session.id for session in imports.source_sessions if session.batch_id is not None}
    mapped_candidates = {mapping.candidate_id for mapping in imports.candidate_mappings}
    active_people = {person.id for person in snapshot.people if person.deleted_at is None}
    known: dict[str, set[str]] = {
        "people": {person.id for person in snapshot.people},
        "affiliations": {row.id for row in snapshot.affiliations},
        "facts": {row.id for row in snapshot.facts},
        "observations": {row.id for row in snapshot.observations},
        "traits": {row.id for row in snapshot.traits},
        "interactions": {row.id for row in snapshot.interactions},
        "relationships": {row.id for row in snapshot.relationships},
    }

    # One batch belongs to one receipt. Sharing a batch would leave the store's own
    # batch-to-session lookup returning either row, so a later commit could attribute its
    # mappings and status change to the receipt that does not own the batch.
    details: list[str] = _repeated(
        "source session batch",
        (session.batch_id for session in imports.source_sessions if session.batch_id is not None),
    )
    staged_by_batch: dict[str, set[str]] = {}
    # A reference names a person and only a person: the stager mints candidate ids for person
    # candidates alone and rewrites every ref through that one map. Commit builds its resolution
    # map the same way, so a dependant pointing at any other row is not merely odd — it can never
    # resolve, while the claim keeps suppressing a restage of the source that would fix it.
    people_by_batch: dict[str, set[str]] = {}
    staged_batch_of: dict[str, str] = {}
    for row in imports.staging:
        staged_by_batch.setdefault(row.batch_id, set()).add(row.id)
        staged_batch_of[row.id] = row.batch_id
        if row.candidate.get("type") == "person":
            people_by_batch.setdefault(row.batch_id, set()).add(row.id)

    for mapping in imports.candidate_mappings:
        session = sessions.get(mapping.source_session_id)
        staged_batch = staged_batch_of.get(mapping.candidate_id)
        if staged_batch is not None and staged_batch != mapping.batch_id:
            # The candidate id and the staging row id are the same identifier, and a row lives in
            # one batch, so its mapping is written with that batch. Filed under another, the
            # mapping is invisible to the commit that would use it even though both halves look
            # internally consistent — each agrees with its own session.
            details.append(
                f"candidate mapping {mapping.candidate_id} is filed under {mapping.batch_id} but its staging "
                f"row belongs to {staged_batch}"
            )
        if session is None:
            details.append(
                f"candidate mapping {mapping.candidate_id} references an unbundled source session: "
                f"{mapping.source_session_id}"
            )
        elif mapping.batch_id != session.batch_id:
            # Commit reads a batch's mappings by the receipt's own batch id, so a mapping filed
            # under a different one is invisible to it: an already-committed dependency would
            # silently fall back to name matching instead of resolving through its outcome.
            details.append(
                f"candidate mapping {mapping.candidate_id} belongs to a batch its source session "
                f"does not own: {mapping.batch_id}"
            )
        if mapping.disposition != "entity":
            continue
        collection = _MAPPED_ENTITY_COLLECTIONS.get(mapping.entity_type)
        if collection is None:
            details.append(
                f"candidate mapping {mapping.candidate_id} names an unsupported entity type: {mapping.entity_type}"
            )
        elif mapping.entity_id not in known[collection]:
            details.append(
                f"candidate mapping {mapping.candidate_id} references an unbundled {mapping.entity_type}: "
                f"{mapping.entity_id}"
            )
        elif mapping.entity_type == "person" and mapping.entity_id not in active_people:
            # A merge retargets person mappings to the survivor and a forget removes them, so a
            # live mapping never points at a retired identity. Restoring one would let a later
            # commit resolve a dependant through it and then fail its own active-person check.
            details.append(
                f"candidate mapping {mapping.candidate_id} references a person who is not active in the "
                f"bundle: {mapping.entity_id}"
            )
    for row in imports.staging:
        if row.batch_id not in batches:
            details.append(f"staging row {row.id} references a batch with no bundled source session: {row.batch_id}")
        matched = row.candidate.get("matched_person_id")
        if isinstance(matched, str) and matched not in active_people:
            details.append(f"staging row {row.id} matches a person who is not active in the bundle: {matched}")
        # A mapping is written in the same unit of work as the row's transition to committed, so
        # the two never disagree in state this installation produced, in either direction. The
        # spec puts it as an invariant of the mapping: "a committed status can never become
        # visible without its output mapping".
        if row.status != "committed" and row.id in mapped_candidates:
            # Restoring a pending row that already has an outcome would let commit write a second
            # entity and overwrite the mapping, orphaning the first from the source that made it.
            details.append(f"staging row {row.id} is pending but already has a committed outcome")
        elif row.status == "committed" and row.id not in mapped_candidates:
            # And a committed row without its outcome is a dependency commit cannot resolve
            # through provenance, so it falls back to matching the stored name — which is the
            # heuristic the mapping exists to replace, and which resolves to a different identity
            # once names are ambiguous.
            details.append(f"staging row {row.id} is committed but carries no outcome mapping")
        # References are batch-local, so a dependant naming a candidate the bundle does not carry
        # would restore a batch whose commit can never resolve it.
        references = staged_candidate_references(row.candidate)
        unknown = references - staged_by_batch.get(row.batch_id, set())
        details.extend(
            f"staging row {row.id} references a candidate outside its batch: {reference}"
            for reference in sorted(unknown)
        )
        # And one carried by the batch but of the wrong type is just as unresolvable, because the
        # resolution map commit builds holds person rows only. (When M18.3 adds evidence
        # references to observation and interaction candidates, this is the rule that widens.)
        mistyped = (references - unknown) - people_by_batch.get(row.batch_id, set())
        details.extend(
            f"staging row {row.id} references a candidate that is not a person: {reference}"
            for reference in sorted(mistyped)
        )
    details.extend(_emptied_session_details(imports))
    return details


def _emptied_session_details(imports: BundleImportState) -> list[str]:
    """Refuse a live receipt the bundle leaves with nothing behind it.

    A non-redacted receipt is one duplicate detection will report as an existing import, so the
    caller is told their source is already here and pointed at that batch. If the bundle carries
    neither a durable mapping nor a reviewable row for it, that report is a dead end: `review`
    finds nothing, the count describes nothing, and the only way past it is to abandon the
    duplicate rule. Hard forget never leaves that state — when its erasure empties a receipt it
    reduces it to a terminal `redacted` claim or deletes it outright — so a bundle carrying one
    was not produced by this installation.

    A *partially committed* receipt whose reviewable rows were erased but whose other mappings
    survived is an ordinary outcome of that same forget, and stays accepted: it still owns
    something live. Only having nothing at all is the contradiction.

    The opposite mismatch is refused too, and it is the one that loses data quietly. A receipt's
    status is recomputed from its rows at every commit, so `committed` means none were left
    pending. Export reads that status rather than the rows: it carries staging only for a
    `staged` or `partially_committed` receipt. A restored `committed` receipt still holding a
    pending row would therefore drop that candidate from the very next bundle — and the reduced
    bundle would validate, so nothing downstream would ever notice it had gone.
    """
    pending_batches = {row.batch_id for row in imports.staging if row.status != "committed"}
    mapped_sessions = {mapping.source_session_id for mapping in imports.candidate_mappings}
    details: list[str] = []
    for session in imports.source_sessions:
        if session.status == "redacted":
            continue
        has_pending = session.batch_id is not None and session.batch_id in pending_batches
        if has_pending and session.status not in REVIEWABLE_SESSION_STATUSES:
            details.append(f"source session {session.id} owns a reviewable staging row but is {session.status}")
        elif session.status == "staged" and not has_pending:
            # `staged` means nothing has committed, so mappings cannot stand in for the rows.
            details.append(f"source session {session.id} is staged but owns no reviewable staging row")
        elif not has_pending and session.id not in mapped_sessions:
            details.append(f"source session {session.id} is live but owns no mapping and no reviewable staging row")
    return details


def _duplicate_details(document: SyncBundleDocument) -> list[str]:
    snapshot = document.snapshot
    collections: list[tuple[str, Iterable[str]]] = [
        ("device id", (device.id for device in document.devices)),
        ("changelog op_id", (entry.op_id for entry in document.changelog)),
        ("person id", (person.id for person in snapshot.people)),
        ("alias id", (alias.id for person in snapshot.people for alias in person.aliases)),
        ("organization id", (organization.id for organization in snapshot.organizations)),
        ("affiliation id", (affiliation.id for affiliation in snapshot.affiliations)),
        ("relationship id", (relationship.id for relationship in snapshot.relationships)),
        ("fact id", (fact.id for fact in snapshot.facts)),
        ("observation id", (observation.id for observation in snapshot.observations)),
        ("trait id", (trait.id for trait in snapshot.traits)),
        ("interaction id", (interaction.id for interaction in snapshot.interactions)),
        ("reminder id", (reminder.id for reminder in snapshot.reminders)),
        ("preference key", (preference.key for preference in snapshot.user_preferences)),
        ("audit entry id", (entry.id for entry in snapshot.audit_log)),
        ("relationship type", (row.type for row in document.relationship_vocabulary.types)),
        ("relationship synonym", (row.synonym for row in document.relationship_vocabulary.synonyms)),
        ("source session id", (row.id for row in document.imports.source_sessions)),
        ("source claim", (row.claim_key for row in document.imports.source_sessions if row.claim_key)),
        ("candidate mapping id", (row.candidate_id for row in document.imports.candidate_mappings)),
        ("staging row id", (row.id for row in document.imports.staging)),
    ]
    details = [detail for label, values in collections for detail in _repeated(label, values)]
    for interaction in snapshot.interactions:
        details.extend(
            _repeated(f"participant of interaction {interaction.id}", interaction.participant_ids)
        )
    return details


def _repeated(label: str, values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return [f"duplicate {label}: {value}" for value in sorted(repeated)]


def _origin_details(document: SyncBundleDocument) -> list[str]:
    origins = [device for device in document.devices if device.id == document.origin_device_id]
    if not origins:
        return [f"origin device is absent from the bundle devices: {document.origin_device_id}"]
    if any(device.retired_at is not None for device in origins):
        return [f"origin device is retired in the bundle: {document.origin_device_id}"]
    return []


def _changelog_device_details(document: SyncBundleDocument) -> list[str]:
    known = {device.id for device in document.devices}
    return sorted(
        {
            f"changelog entry references an unbundled device: {entry.device_id}"
            for entry in document.changelog
            if entry.device_id not in known
        }
    )


def _watermark_details(document: SyncBundleDocument) -> list[str]:
    watermark = (document.watermark.hlc_physical_ms, document.watermark.hlc_logical)
    details = [
        f"changelog entry is ahead of the bundle watermark: {entry.op_id}"
        for entry in document.changelog
        if (entry.hlc_physical_ms, entry.hlc_logical) > watermark
    ]
    details.extend(
        f"device clock is ahead of the bundle watermark: {device.id}"
        for device in document.devices
        if (device.hlc_physical_ms, device.hlc_logical) > watermark
    )
    return details


def _reference_details(document: SyncBundleDocument) -> list[str]:
    snapshot = document.snapshot
    people = {person.id for person in snapshot.people}
    organizations = {organization.id for organization in snapshot.organizations}
    # Vocabulary rows genuinely reference each other, so synonym targets and inverses must
    # resolve. A destination always carries the seeded reference vocabulary, so a bundle may
    # name a seeded type it does not itself carry without dangling after restore.
    types = {row.type for row in document.relationship_vocabulary.types} | set(SEEDED_RELATIONSHIP_TYPES)

    details = [
        *_missing("affiliation", ((row.id, row.person_id) for row in snapshot.affiliations), people, "person"),
        *_missing(
            "affiliation", ((row.id, row.org_id) for row in snapshot.affiliations), organizations, "organization"
        ),
        *_missing("relationship", ((row.id, row.subject_id) for row in snapshot.relationships), people, "subject"),
        *_missing("relationship", ((row.id, row.object_id) for row in snapshot.relationships), people, "object"),
        *_missing("fact", ((row.id, row.person_id) for row in snapshot.facts), people, "person"),
        *_missing("observation", ((row.id, row.person_id) for row in snapshot.observations), people, "person"),
        *_missing("trait", ((row.id, row.person_id) for row in snapshot.traits), people, "person"),
        *_missing("reminder", ((row.id, row.person_id) for row in snapshot.reminders), people, "person"),
        *_missing(
            "interaction",
            ((row.id, person_id) for row in snapshot.interactions for person_id in row.participant_ids),
            people,
            "participant",
        ),
        # A stored relationship type deliberately needs no vocabulary row: an unmatched type is
        # legal and reads as category "uncategorized". Requiring one here would make restore
        # refuse a bundle that export legitimately produced.
        *_missing(
            "relationship synonym",
            ((row.synonym, row.type) for row in document.relationship_vocabulary.synonyms),
            types,
            "type",
        ),
    ]
    details.extend(
        _missing(
            "relationship type",
            ((row.type, row.inverse) for row in document.relationship_vocabulary.types if row.inverse is not None),
            types,
            "inverse",
        )
    )
    return details


def _missing(entity: str, pairs: Iterable[tuple[str, str]], known: set[str], reference: str) -> list[str]:
    return [
        f"{entity} {owner} references an unknown {reference}: {value}"
        for owner, value in pairs
        if value not in known
    ]
