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

    A terminal ``redacted`` receipt is what remains after hard forget emptied a source: it must
    still be claim-backed, and it must carry none of the caller-authored or optional inspection
    state that erasure cleared. Enforcing that here means a hand-edited bundle cannot smuggle a
    scrubbed label back into a restored database.
    """

    id: Identifier
    source_kind: str
    label: str | None
    external_source_id: str | None
    content_digest: str | None
    extraction_fingerprint: str | None
    extraction_contract_revision: str | None
    claim_key: str | None
    batch_id: str | None
    status: Literal["staged", "partially_committed", "committed", "redacted"]
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _check_claim(self) -> BundleSourceSession:
        if self.claim_key is not None and self.content_digest is None:
            raise ValueError("a canonical claim requires a content_digest")
        if self.status != "redacted":
            return self
        if self.content_digest is None:
            raise ValueError("a redacted source session must be claim-backed")
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
    """One bundled reviewable staging row, carried only for an incomplete batch."""

    id: Identifier
    batch_id: Identifier
    source: str
    candidate: dict[str, Any]
    status: str
    created_at: UtcDatetime


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

    details: list[str] = []
    for mapping in imports.candidate_mappings:
        if mapping.source_session_id not in sessions:
            details.append(
                f"candidate mapping {mapping.candidate_id} references an unbundled source session: "
                f"{mapping.source_session_id}"
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
    for row in imports.staging:
        if row.batch_id not in batches:
            details.append(f"staging row {row.id} references a batch with no bundled source session: {row.batch_id}")
        matched = row.candidate.get("matched_person_id")
        if isinstance(matched, str) and matched not in active_people:
            details.append(f"staging row {row.id} matches a person who is not active in the bundle: {matched}")
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
