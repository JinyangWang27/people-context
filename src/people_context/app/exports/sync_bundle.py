"""Bootstrap sync-bundle export."""

from __future__ import annotations

import json

from people_context.domain.sync_bundle import (
    SYNC_BUNDLE_FORMAT,
    SYNC_BUNDLE_VERSION,
    BundleCandidateMapping,
    BundleChangelogEntry,
    BundleDevice,
    BundleImportState,
    BundleRelationshipSynonym,
    BundleRelationshipType,
    BundleRelationshipVocabulary,
    BundleSnapshot,
    BundleSourceSession,
    BundleStagingRow,
    BundleTraitEvidence,
    BundleWatermark,
    SyncBundleDocument,
)
from people_context.ports.clock import Clock
from people_context.ports.sync_bundle import BundleReader

SYNC_BUNDLE_FILENAME = "people-context-sync-bundle.json"


class ExportSyncBundle:
    """Build one strict, versioned bootstrap bundle from a single database snapshot."""

    def __init__(self, reader: BundleReader, clock: Clock) -> None:
        self._reader = reader
        self._clock = clock

    def execute(self) -> SyncBundleDocument:
        """Return the validated bundle document for the current snapshot."""
        source = self._reader.read_bundle()
        return SyncBundleDocument(
            format=SYNC_BUNDLE_FORMAT,
            version=SYNC_BUNDLE_VERSION,
            created_at=self._clock.now(),
            origin_device_id=source.origin_device_id,
            watermark=BundleWatermark(
                hlc_physical_ms=source.watermark.physical_ms,
                hlc_logical=source.watermark.logical_counter,
            ),
            devices=[BundleDevice.model_validate(row) for row in source.devices],
            snapshot=BundleSnapshot.model_validate(source.snapshot.__dict__),
            relationship_vocabulary=BundleRelationshipVocabulary(
                types=[BundleRelationshipType.model_validate(row) for row in source.relationship_types],
                synonyms=[BundleRelationshipSynonym.model_validate(row) for row in source.relationship_synonyms],
            ),
            changelog=[
                BundleChangelogEntry.model_validate(entry.model_dump(mode="json")) for entry in source.changelog
            ],
            imports=BundleImportState(
                source_sessions=[BundleSourceSession.model_validate(row) for row in source.source_sessions],
                candidate_mappings=[
                    BundleCandidateMapping.model_validate(row) for row in source.candidate_mappings
                ],
                staging=[BundleStagingRow.model_validate(row) for row in source.staging],
            ),
            trait_evidence=[BundleTraitEvidence.model_validate(row) for row in source.trait_evidence],
        )


def render_bundle_json(document: SyncBundleDocument) -> str:
    """Render one bundle as canonical JSON text.

    Keys are sorted and separators are fixed, so the same snapshot and clock always
    produce byte-identical output regardless of insertion order inside opaque payloads.
    """
    return json.dumps(document.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
