"""Explicit routing for supported import extractors."""

from __future__ import annotations

from typing import Final

from people_context.adapters.importers.email import EmailImportExtractor
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.ics import IcsImportExtractor
from people_context.adapters.importers.linkedin import LinkedInImportExtractor
from people_context.adapters.importers.outlook import OutlookImportExtractor
from people_context.adapters.importers.vcard import VCardImportExtractor
from people_context.adapters.importers.whatsapp import WhatsAppImportExtractor
from people_context.ports.imports import ExtractedImport, ImportExtractor

#: The accepted `source_type` values, declared once beside the dispatch that implements them
#: so a caller that has to offer the choice — the `pctx import` group — cannot drift from it.
SUPPORTED_IMPORT_SOURCES: Final[tuple[str, ...]] = (
    "email",
    "mbox",
    "vcard",
    "ics",
    "linkedin",
    "outlook",
    "whatsapp",
)


def _unsupported_source_type() -> ImportExtractionError:
    quoted = [f"'{source}'" for source in SUPPORTED_IMPORT_SOURCES]
    accepted = f"{', '.join(quoted[:-1])}, or {quoted[-1]}"
    return ImportExtractionError("invalid_source_type", f"source_type must be {accepted}")


class ImportExtractorRouter:
    """Route supported source types to a dedicated extractor."""

    def __init__(self) -> None:
        self._email = EmailImportExtractor()
        self._vcard = VCardImportExtractor()
        self._ics = IcsImportExtractor()
        self._linkedin = LinkedInImportExtractor()
        self._outlook = OutlookImportExtractor()
        self._whatsapp = WhatsAppImportExtractor()

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
        content_bytes: bytes | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
    ) -> ExtractedImport:
        """Extract candidates with the extractor registered for ``source_type``."""
        extractor: ImportExtractor
        if source_type in {"email", "mbox"}:
            extractor = self._email
        elif source_type == "vcard":
            extractor = self._vcard
        elif source_type == "ics":
            extractor = self._ics
        elif source_type == "linkedin":
            extractor = self._linkedin
        elif source_type == "outlook":
            extractor = self._outlook
        elif source_type == "whatsapp":
            extractor = self._whatsapp
        else:
            raise _unsupported_source_type()
        return extractor.extract(
            source_type,
            content=content,
            path=path,
            self_addresses=self_addresses,
            self_names=self_names,
            self_sender=self_sender,
            content_bytes=content_bytes,
            max_source_bytes=max_source_bytes,
            max_candidates=max_candidates,
        )
