"""Shared import value-normalization tests."""

from __future__ import annotations

import pytest

from people_context.adapters.importers.normalization import clean_text, normalize_email


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("alice@example.com", "alice@example.com"),
        ("ALICE@Example.COM", "alice@example.com"),
        ("  alice@example.com  ", "alice@example.com"),
        ("first.last+tag@sub.example.co.uk", "first.last+tag@sub.example.co.uk"),
    ],
)
def test_normalize_email_folds_only_case_and_surrounding_space(value: str, expected: str) -> None:
    assert normalize_email(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-an-address",
        "alice@",
        "@example.com",
        "alice example@example.com",
        # An internationalized address must be rejected, never folded onto the distinct ASCII
        # address that stripping its combining marks would produce.
        "josé@example.com",
        "bob@exämple.com",
    ],
)
def test_normalize_email_rejects_values_outside_the_supported_ascii_form(value: str) -> None:
    assert normalize_email(value) is None


def test_normalize_email_never_rewrites_an_international_address_to_an_ascii_one() -> None:
    assert normalize_email("josé@example.com") != normalize_email("jose@example.com")
    assert normalize_email("jose@example.com") == "jose@example.com"


def test_clean_text_collapses_whitespace_and_treats_non_strings_as_empty() -> None:
    assert clean_text("  Alice   Q  Example ") == "Alice Q Example"
    assert clean_text(None) == ""
    assert clean_text(["extra", "columns"]) == ""
