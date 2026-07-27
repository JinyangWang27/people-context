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
SYNC_BUNDLE_VERSION = 1

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


class SyncBundleDocument(StrictBundleModel):
    """One complete, point-in-time bootstrap bundle."""

    format: Literal["people-context-sync-bundle"]
    version: Literal[1]
    created_at: UtcDatetime
    origin_device_id: Identifier
    watermark: BundleWatermark
    devices: list[BundleDevice]
    snapshot: BundleSnapshot
    relationship_vocabulary: BundleRelationshipVocabulary
    changelog: list[BundleChangelogEntry]


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
    ]
    if details:
        raise InvalidBundleError(details)


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
    # A destination always carries the seeded reference vocabulary, so a bundle may reference a
    # seeded type it does not itself carry without dangling after restore.
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
        *_missing("relationship", ((row.id, row.type) for row in snapshot.relationships), types, "type"),
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
