"""Tests for shared domain value objects and helpers."""

from __future__ import annotations

from datetime import UTC, date

import pytest

from people_context.domain.shared import (
    ValidityPeriod,
    new_id,
    normalize_name,
    utc_now,
)


def test_normalize_name_width_folding() -> None:
    # Fullwidth Latin -> ASCII via NFKC, then casefold.
    assert normalize_name("ＡＢＣ") == "abc"


def test_normalize_name_casefold() -> None:
    assert normalize_name("HeLLo World") == "hello world"


def test_normalize_name_strips_diacritics() -> None:
    assert normalize_name("José") == "jose"
    assert normalize_name("José") == normalize_name("jose")


def test_normalize_name_cjk_passthrough() -> None:
    assert normalize_name("周易") == "周易"


def test_normalize_name_collapses_whitespace() -> None:
    assert normalize_name("  Jin   yang \tWang ") == "jin yang wang"


def test_validity_period_ok() -> None:
    period = ValidityPeriod(valid_from=date(2020, 1, 1), valid_to=date(2021, 1, 1))
    assert period.valid_from < period.valid_to


def test_validity_period_equal_dates_ok() -> None:
    d = date(2020, 1, 1)
    period = ValidityPeriod(valid_from=d, valid_to=d)
    assert period.valid_from == period.valid_to


def test_validity_period_inverted_raises() -> None:
    with pytest.raises(ValueError):
        ValidityPeriod(valid_from=date(2021, 1, 1), valid_to=date(2020, 1, 1))


def test_validity_period_open_ended_ok() -> None:
    assert ValidityPeriod().valid_from is None
    assert ValidityPeriod(valid_from=date(2020, 1, 1)).valid_to is None


def test_new_id_format_and_ordering() -> None:
    first = new_id()
    second = new_id()
    assert len(first) == 26
    assert len(second) == 26
    # ULIDs are time-sortable; the second minted id sorts >= the first.
    assert second >= first


def test_utc_now_is_tz_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)
