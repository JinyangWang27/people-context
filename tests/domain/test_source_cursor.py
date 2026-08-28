"""A pagination cursor is bounded and validated before it can reach a query."""

from __future__ import annotations

import base64

import pytest

from people_context.domain.source_cursor import (
    MAX_CURSOR_CHARS,
    MAX_CURSOR_ID_CHARS,
    decode_cursor,
    encode_cursor,
)


def _cursor(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def test_a_cursor_round_trips_its_identifier_exactly() -> None:
    assert decode_cursor(encode_cursor("01JZ0000000000000000000001")) == "01JZ0000000000000000000001"


def test_a_cursor_carries_the_identifier_and_nothing_else() -> None:
    """The encoding is reversible, so what it holds is what it discloses.

    A source listing can end a page on a terminal redacted receipt, whose timestamps inspection
    withholds. Encoding a sort key would have handed that timestamp to the caller through the one
    value they are told to pass back, so the cursor holds only the id — which is disclosed anyway.
    """
    cursor = encode_cursor("01JZ0000000000000000000001")

    decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")

    assert decoded == "01JZ0000000000000000000001"


def test_an_identifier_a_bootstrap_restore_accepts_round_trips() -> None:
    """Restore preserves ids verbatim and requires only non-blank, so nothing narrower fits.

    A database restored from another implementation can hold ids that are neither ULIDs nor
    ASCII. Refusing them here would leave their provenance visible but impossible to page.
    """
    for identifier in ("obs-1", "urn:uuid:9f8a/7b+6c", "источник-42", "id with spaces", "A" * 200):
        assert decode_cursor(encode_cursor(identifier)) == identifier


def test_an_identifier_at_the_bound_is_accepted() -> None:
    identifier = "A" * MAX_CURSOR_ID_CHARS

    assert decode_cursor(encode_cursor(identifier)) == identifier


def test_an_identifier_past_the_bound_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid pagination cursor"):
        decode_cursor(encode_cursor("A" * (MAX_CURSOR_ID_CHARS + 1)))


def test_surrounding_whitespace_is_trimmed_from_the_cursor_but_not_its_payload() -> None:
    """Base64 has no meaningful surrounding whitespace; an identifier's whitespace is its own."""
    identifier = " padded-id "

    assert decode_cursor(f"  {encode_cursor(identifier)}  ") == identifier


def test_an_oversized_cursor_is_refused_without_being_decoded() -> None:
    with pytest.raises(ValueError, match=f"at most {MAX_CURSOR_CHARS} characters"):
        decode_cursor("A" * (MAX_CURSOR_CHARS + 1))


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        decode_cursor(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "!!! not base64 !!!",
        # Valid base64 whose bytes are not UTF-8.
        base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii").rstrip("="),
        # A cursor decoding to whitespace names no row.
        _cursor("   "),
    ],
)
def test_a_malformed_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(raw)


def test_a_cursor_refusal_never_echoes_what_was_supplied() -> None:
    """Refusals name the rule. A cursor is caller-supplied text and is not repeated back."""
    secret = _cursor("Interview with Alice " + "A" * MAX_CURSOR_ID_CHARS)

    with pytest.raises(ValueError) as raised:
        decode_cursor(secret)

    assert "Alice" not in str(raised.value)
    assert secret not in str(raised.value)
