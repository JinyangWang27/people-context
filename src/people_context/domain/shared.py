"""Shared value objects, enums, and helpers for the domain layer."""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator
from ulid import ULID


class Sensitivity(StrEnum):
    """Disclosure sensitivity level of an assertive record."""

    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Provenance(BaseModel):
    """Where an assertion came from."""

    source: str
    session: str | None = None
    stated_by: str | None = None


class ValidityPeriod(BaseModel):
    """A closed/open date range during which an assertion holds."""

    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _check_order(self) -> ValidityPeriod:
        if self.valid_from is not None and self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValueError("valid_from must be <= valid_to")
        return self

    def overlaps(self, other: ValidityPeriod) -> bool:
        """Return whether two periods share at least one day (open bounds are unbounded)."""
        start = max(self.valid_from or date.min, other.valid_from or date.min)
        end = min(self.valid_to or date.max, other.valid_to or date.max)
        return start <= end

    def contains(self, as_of: date) -> bool:
        """Return whether ``as_of`` falls inside this period (open bounds are unbounded)."""
        return (self.valid_from is None or self.valid_from <= as_of) and (
            self.valid_to is None or self.valid_to >= as_of
        )


def new_id() -> str:
    """Return a fresh ULID string (26-char Crockford, sortable)."""
    return str(ULID())


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Return one comparable UTC instant for a stored timestamp.

    Stored timestamps keep whatever offset the writer supplied, and the write contract still
    accepts naive values. Comparing instants therefore converts an aware value and reads a
    naive one as UTC. The host timezone is never consulted, which is exactly what
    ``astimezone()`` or ``timestamp()`` on a naive value would silently do — and what would
    otherwise make an ordering, and any budget that truncates an ordered list, depend on the
    machine that happened to run the read.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_name(value: str) -> str:
    """Normalize a name for matching.

    NFKC -> casefold -> strip combining marks (NFD, drop category Mn) ->
    collapse internal whitespace -> strip.
    """
    text = unicodedata.normalize("NFKC", value).casefold()
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(without_marks.split())
