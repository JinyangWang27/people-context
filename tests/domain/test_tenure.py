"""Tenure periods: bounds known to a year, a month, or a day, stored as span edges."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from people_context.domain.shared import ValidityPeriod
from people_context.domain.tenure import DatePrecision, PartialDate, TenurePeriod, format_bound, span_end, span_start


class _Holder(BaseModel):
    value: PartialDate


@pytest.mark.parametrize(
    ("text", "start", "end", "precision"),
    [
        ("2015", date(2015, 1, 1), date(2015, 12, 31), DatePrecision.YEAR),
        ("2015-11", date(2015, 11, 1), date(2015, 11, 30), DatePrecision.MONTH),
        ("2024-02", date(2024, 2, 1), date(2024, 2, 29), DatePrecision.MONTH),
        ("2015-11-03", date(2015, 11, 3), date(2015, 11, 3), DatePrecision.DAY),
    ],
)
def test_a_partial_date_names_a_span(text: str, start: date, end: date, precision: DatePrecision) -> None:
    assert span_start(text) == (start, precision)
    assert span_end(text) == (end, precision)


@pytest.mark.parametrize("text", ["15", "2015-13", "2015-02-30", "2015/11", "Nov 2015", "2015-1", ""])
def test_anything_but_a_partial_iso_date_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        _Holder(value=text)


def test_a_date_is_accepted_as_a_day() -> None:
    assert _Holder(value=date(2015, 11, 3)).value == "2015-11-03"  # type: ignore[arg-type]


def test_a_stated_range_keeps_each_bound_at_its_own_precision() -> None:
    period = TenurePeriod.from_partial("2015", "2019-06")

    assert (period.valid_from, period.valid_to) == (date(2015, 1, 1), date(2019, 6, 30))
    assert (period.valid_from_precision, period.valid_to_precision) == (DatePrecision.YEAR, DatePrecision.MONTH)
    assert (period.from_text, period.to_text) == ("2015", "2019-06")


def test_span_edges_keep_range_reads_correct_without_knowing_precision() -> None:
    period = TenurePeriod.from_partial("2015", "2019")

    assert period.contains(date(2019, 12, 31))
    assert not period.contains(date(2020, 1, 1))
    assert period.overlaps(ValidityPeriod(valid_from=date(2019, 7, 1)))


def test_one_year_on_both_ends_is_that_whole_year() -> None:
    period = TenurePeriod.from_partial("2015", "2015")

    assert (period.valid_from, period.valid_to) == (date(2015, 1, 1), date(2015, 12, 31))


def test_an_inverted_range_is_refused() -> None:
    with pytest.raises(ValidationError):
        TenurePeriod.from_partial("2016", "2015")


@pytest.mark.parametrize(
    "fields",
    [
        {"valid_from": date(2015, 3, 1), "valid_from_precision": "year"},
        {"valid_from": date(2015, 3, 2), "valid_from_precision": "month"},
        {"valid_to": date(2015, 12, 30), "valid_to_precision": "year"},
        {"valid_to": date(2015, 11, 1), "valid_to_precision": "month"},
        {"valid_from_precision": "year"},
        {"valid_to_precision": "month"},
    ],
)
def test_a_precision_its_date_contradicts_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TenurePeriod.model_validate(fields)


def test_an_exact_validity_period_is_accepted_as_days() -> None:
    period = TenurePeriod.model_validate(ValidityPeriod(valid_from=date(2020, 3, 4)))

    assert period == TenurePeriod(valid_from=date(2020, 3, 4))
    assert period.valid_from_precision is DatePrecision.DAY


def test_format_renders_only_the_stated_part() -> None:
    assert format_bound(date(2015, 1, 1), DatePrecision.YEAR) == "2015"
    assert format_bound(date(2015, 11, 1), DatePrecision.MONTH) == "2015-11"
    assert format_bound(date(2015, 11, 3), DatePrecision.DAY) == "2015-11-03"
    assert format_bound(None, DatePrecision.DAY) is None
