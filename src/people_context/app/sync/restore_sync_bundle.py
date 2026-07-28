"""Bootstrap sync-bundle restore."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from people_context.domain.sync_bundle import (
    InvalidBundleError,
    SyncBundleDocument,
    validate_bundle_document,
)
from people_context.ports.bootstrap_restore import BootstrapRestorer, RestoreOutcome


@dataclass(frozen=True)
class RestorePreview:
    """What one parsed bundle would write, shown before any confirmation or reservation."""

    created_at: datetime
    origin_device_id: str
    watermark: tuple[int, int]
    counts: dict[str, int]


class RestoreSyncBundle:
    """Parse, validate, preview, and restore one bootstrap bundle.

    Parsing and document validation are deliberately separate from :meth:`execute` so a
    caller can refuse a malformed document before previewing counts, prompting the user, or
    letting the restorer reserve the destination for writing.
    """

    def __init__(self, restorer: BootstrapRestorer) -> None:
        self._restorer = restorer

    def parse(self, text: str) -> SyncBundleDocument:
        """Return the fully validated document, or raise ``InvalidBundleError``."""
        try:
            document = SyncBundleDocument.model_validate_json(text)
        except ValidationError as exc:
            raise InvalidBundleError(_structural_details(exc)) from exc
        validate_bundle_document(document)
        return document

    def preview(self, document: SyncBundleDocument) -> RestorePreview:
        """Summarize a validated document without touching the destination."""
        snapshot = document.snapshot
        vocabulary = document.relationship_vocabulary
        return RestorePreview(
            created_at=document.created_at,
            origin_device_id=document.origin_device_id,
            watermark=(document.watermark.hlc_physical_ms, document.watermark.hlc_logical),
            counts={
                "people": len(snapshot.people),
                "organizations": len(snapshot.organizations),
                "affiliations": len(snapshot.affiliations),
                "relationships": len(snapshot.relationships),
                "facts": len(snapshot.facts),
                "observations": len(snapshot.observations),
                "traits": len(snapshot.traits),
                "interactions": len(snapshot.interactions),
                "reminders": len(snapshot.reminders),
                "preferences": len(snapshot.user_preferences),
                "audit entries": len(snapshot.audit_log),
                "relationship types": len(vocabulary.types),
                "relationship synonyms": len(vocabulary.synonyms),
                "devices": len(document.devices),
                "changelog entries": len(document.changelog),
            },
        )

    def execute(self, document: SyncBundleDocument) -> RestoreOutcome:
        """Restore a validated document, or raise the restorer's structured refusal."""
        return self._restorer.restore(document)


def _structural_details(error: ValidationError) -> list[str]:
    """Render field locations and messages only, never the rejected input values."""
    return [f"{_location(issue['loc'])}: {issue['msg']}" for issue in error.errors()]


def _location(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location) or "document"
