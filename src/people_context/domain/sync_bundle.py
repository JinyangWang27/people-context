"""Strict, versioned bootstrap sync-bundle contracts.

These models are the restore contract, not a loose ``dict[str, Any]`` envelope. Every
model forbids unknown fields, no field carries a silent default, timestamps must be
timezone-aware and are normalized to UTC, and identifiers must be non-blank. Structural
validation therefore fails closed before any consumer inspects the document.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from people_context.domain.person import AliasKind
from people_context.domain.reminder import ReminderKind, ReminderStatus
from people_context.domain.shared import Confidence, Sensitivity
from people_context.domain.trait import TraitCategory

SYNC_BUNDLE_FORMAT = "people-context-sync-bundle"
SYNC_BUNDLE_VERSION = 1


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


Identifier = Annotated[str, AfterValidator(_require_non_blank)]
UtcDatetime = Annotated[datetime, BeforeValidator(_reject_numeric_timestamp), AfterValidator(_require_utc)]
HlcComponent = Annotated[int, Field(ge=0)]


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
    schema_version: int
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
