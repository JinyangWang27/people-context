"""The shared failure type every import extractor raises."""

from __future__ import annotations


class ImportExtractionError(Exception):
    """Raised when import source parameters or headers are invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
