"""Validity periods whose bounds are known only to a year or a month.

A CV or a LinkedIn profile says "2015–2019" or "Nov 2024 – Jan 2026", never a day. Storing
2015-01-01 for "2015" would invent a day nobody stated, and leaving the date out loses the year.
A `TenurePeriod` keeps both halves of the truth: each bound is the edge of the span the source
named — the first day of a start, the last day of an end — and a precision says how much of that
date was actually stated.

Because the bounds are real span edges, a `TenurePeriod` is still a `ValidityPeriod`: `overlaps`
and `contains` answer correctly for "2015" without knowing about precision at all, so every reader
of an affiliation's period keeps working unchanged. Only a reader that *displays* a bound needs the
precision, through `format_bound`.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BeforeValidator, model_validator

from people_context.domain.shared import ValidityPeriod


class DatePrecision(StrEnum):
    """How much of a stored bound the source actually stated."""

    DAY = "day"
    MONTH = "month"
    YEAR = "year"


_PARTIAL_DATE: Final = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def _normalize_partial_date(value: object) -> object:
    """Return `YYYY`, `YYYY-MM`, or `YYYY-MM-DD` for a date or a partial ISO date, or refuse it."""
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return value  # left for the str type to refuse
    text = value.strip()
    match = _PARTIAL_DATE.fullmatch(text)
    if match is None:
        raise ValueError("expected a date as YYYY, YYYY-MM, or YYYY-MM-DD")
    year, month, day = match.groups()
    # Constructing the date is the range check for the month and the day.
    date(int(year), int(month or 1), int(day or 1))
    return text


#: A date the caller knows to a year (`2015`), a month (`2015-11`), or a day (`2015-11-03`).
#: A `date` is accepted too and means a day. The stated text is kept as written, so a staged
#: candidate shows the reviewer exactly the precision the source gave.
PartialDate = Annotated[str, BeforeValidator(_normalize_partial_date)]


def span_start(value: str | date) -> tuple[date, DatePrecision]:
    """Return the first day of the span a partial date names, and its precision."""
    text = str(_normalize_partial_date(value))
    parts = [int(part) for part in text.split("-")]
    if len(parts) == 1:
        return date(parts[0], 1, 1), DatePrecision.YEAR
    if len(parts) == 2:
        return date(parts[0], parts[1], 1), DatePrecision.MONTH
    return date(parts[0], parts[1], parts[2]), DatePrecision.DAY


def span_end(value: str | date) -> tuple[date, DatePrecision]:
    """Return the last day of the span a partial date names, and its precision."""
    start, precision = span_start(value)
    if precision is DatePrecision.YEAR:
        return date(start.year, 12, 31), precision
    if precision is DatePrecision.MONTH:
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]), precision
    return start, precision


def format_bound(value: date | None, precision: DatePrecision) -> str | None:
    """Render a stored bound to exactly the precision it was stated with."""
    if value is None:
        return None
    if precision is DatePrecision.YEAR:
        return f"{value.year:04d}"
    if precision is DatePrecision.MONTH:
        return f"{value.year:04d}-{value.month:02d}"
    return value.isoformat()


class TenurePeriod(ValidityPeriod):
    """A validity period whose bounds may be known only to a month or a year.

    The bounds are span edges — `valid_from` the first day of the stated start, `valid_to` the last
    day of the stated end — and the validator refuses any other day, so a stored row can never
    claim a precision its date contradicts. An absent bound carries `day`, the neutral default.
    """

    valid_from_precision: DatePrecision = DatePrecision.DAY
    valid_to_precision: DatePrecision = DatePrecision.DAY

    @model_validator(mode="after")
    def _check_precision(self) -> TenurePeriod:
        if self.valid_from is not None and span_start(self.from_text or "")[0] != self.valid_from:
            raise ValueError("valid_from must be the first day of its stated month or year")
        if self.valid_to is not None and span_end(self.to_text or "")[0] != self.valid_to:
            raise ValueError("valid_to must be the last day of its stated month or year")
        if self.valid_from is None and self.valid_from_precision is not DatePrecision.DAY:
            raise ValueError("an absent valid_from has no precision")
        if self.valid_to is None and self.valid_to_precision is not DatePrecision.DAY:
            raise ValueError("an absent valid_to has no precision")
        return self

    @classmethod
    def from_partial(cls, valid_from: str | date | None, valid_to: str | date | None) -> TenurePeriod:
        """Build the period a source stated, e.g. `from_partial("2015", "2019-06")`."""
        start, start_precision = span_start(valid_from) if valid_from is not None else (None, DatePrecision.DAY)
        end, end_precision = span_end(valid_to) if valid_to is not None else (None, DatePrecision.DAY)
        return cls(
            valid_from=start,
            valid_to=end,
            valid_from_precision=start_precision,
            valid_to_precision=end_precision,
        )

    @model_validator(mode="before")
    @classmethod
    def _widen_exact_period(cls, value: object) -> object:
        # A plain `ValidityPeriod` states exact days, so it is accepted as one: every affiliation
        # written before precision existed, and every caller still building one, keeps working.
        if isinstance(value, ValidityPeriod) and not isinstance(value, TenurePeriod):
            return {"valid_from": value.valid_from, "valid_to": value.valid_to}
        return value

    @property
    def from_text(self) -> str | None:
        """The start as it was stated: `2015`, `2015-11`, or `2015-11-03`."""
        return format_bound(self.valid_from, self.valid_from_precision)

    @property
    def to_text(self) -> str | None:
        """The end as it was stated: `2019`, `2019-06`, or `2019-06-30`."""
        return format_bound(self.valid_to, self.valid_to_precision)
