"""Explicit routing for supported import extractors."""

from __future__ import annotations

from people_context.adapters.email_import import EmailImportExtractor, ImportExtractionError
from people_context.adapters.ics_import import IcsImportExtractor
from people_context.adapters.vcard_import import VCardImportExtractor
from people_context.ports.imports import ExtractedImport


class ImportExtractorRouter:
    """Route supported source types to a dedicated extractor."""

    def __init__(self) -> None:
        self._email = EmailImportExtractor()
        self._vcard = VCardImportExtractor()
        self._ics = IcsImportExtractor()

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
    ) -> ExtractedImport:
        """Extract candidates with the extractor registered for ``source_type``."""
        if source_type in {"email", "mbox"}:
            return self._email.extract(
                source_type,
                content=content,
                path=path,
                self_addresses=self_addresses,
            )
        if source_type == "vcard":
            return self._vcard.extract(
                source_type,
                content=content,
                path=path,
                self_addresses=self_addresses,
            )
        if source_type == "ics":
            return self._ics.extract(
                source_type,
                content=content,
                path=path,
                self_addresses=self_addresses,
            )
        raise ImportExtractionError(
            "invalid_source_type",
            "source_type must be 'email', 'mbox', 'vcard', or 'ics'",
        )
