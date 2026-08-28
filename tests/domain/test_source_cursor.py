"""A pagination cursor is scoped and validated before it can reach a query."""

from __future__ import annotations

import base64

import pytest

from people_context.domain.source_cursor import (
    SCOPE_DIGEST_CHARS,
    SOURCE_LIST_SCOPE,
    decode_cursor,
    encode_cursor,
    mapping_scope,
)


def _raw(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def test_a_cursor_round_trips_its_key_within_its_own_scope() -> None:
    cursor = encode_cursor(SOURCE_LIST_SCOPE, "01JZ0000000000000000000001")

    assert decode_cursor(cursor, scope=SOURCE_LIST_SCOPE) == "01JZ0000000000000000000001"


def test_a_cursor_carries_no_sort_key() -> None:
    """The encoding is reversible, so what it holds is what it discloses.

    A source listing can end a page on a terminal redacted receipt, whose timestamps inspection
    withholds. Encoding a sort key would have handed that timestamp to the caller through the one
    value they are told to pass back, so the cursor holds only a scope tag and the id.
    """
    cursor = encode_cursor(SOURCE_LIST_SCOPE, "01JZ0000000000000000000001")

    decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")

    assert decoded[SCOPE_DIGEST_CHARS:] == "01JZ0000000000000000000001"
    assert len(decoded[:SCOPE_DIGEST_CHARS]) == SCOPE_DIGEST_CHARS


def test_one_source_mapping_cursor_is_refused_by_another_source() -> None:
    """Otherwise it would be an arbitrary boundary in the other source's mappings.

    The query would succeed and silently omit everything sorting below it — a wrong answer
    presented as a complete page.
    """
    cursor = encode_cursor(mapping_scope("SESSION-B"), "CANDIDATE-9")

    with pytest.raises(ValueError, match="issued for a different listing"):
        decode_cursor(cursor, scope=mapping_scope("SESSION-A"))


def test_a_listing_cursor_is_refused_by_a_mapping_page_and_the_reverse() -> None:
    listing = encode_cursor(SOURCE_LIST_SCOPE, "SESSION-A")
    mappings = encode_cursor(mapping_scope("SESSION-A"), "CANDIDATE-1")

    with pytest.raises(ValueError, match="issued for a different listing"):
        decode_cursor(listing, scope=mapping_scope("SESSION-A"))
    with pytest.raises(ValueError, match="issued for a different listing"):
        decode_cursor(mappings, scope=SOURCE_LIST_SCOPE)


def test_a_scope_tag_is_fixed_width_so_any_identifier_byte_survives() -> None:
    """Both halves are format-opaque, so a delimiter could occur inside either one."""
    for session_id, candidate_id in (
        ("has:colons:everywhere", "9:also:colons"),
        ("unit\x1fseparator", "another\x1fone"),
        ("12:looks-like-a-prefix", "34:so-does-this"),
        ("источник", "кандидат"),
    ):
        scope = mapping_scope(session_id)
        assert decode_cursor(encode_cursor(scope, candidate_id), scope=scope) == candidate_id


def test_an_identifier_a_bootstrap_restore_accepts_round_trips() -> None:
    """Restore preserves ids verbatim and requires only non-blank, so nothing narrower fits.

    A database restored from another implementation can hold ids that are neither ULIDs nor
    ASCII nor short. Refusing them would leave their provenance visible but impossible to page.
    """
    for identifier in ("obs-1", "urn:uuid:9f8a/7b+6c", "источник-42", " padded ", "A" * 1000):
        assert decode_cursor(encode_cursor(SOURCE_LIST_SCOPE, identifier), scope=SOURCE_LIST_SCOPE) == identifier


def test_a_cursor_this_surface_issues_is_always_one_it_accepts() -> None:
    """The invariant every previous length ceiling broke: encode and decode must agree.

    Two very long ids scope and key one mapping cursor. Under any fixed cursor ceiling, the first
    page would succeed and hand back a continuation its own next invocation refused.
    """
    scope = mapping_scope("S" * 4000)
    key = "C" * 4000

    assert decode_cursor(encode_cursor(scope, key), scope=scope) == key


def test_a_scope_tag_keeps_a_cursor_independent_of_its_scoping_identifier() -> None:
    """A long session id must not inflate every cursor of that source's mapping page."""
    short = encode_cursor(mapping_scope("S1"), "C1")
    long_scope = encode_cursor(mapping_scope("S" * 4000), "C1")

    assert len(short) == len(long_scope)


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        decode_cursor(raw, scope=SOURCE_LIST_SCOPE)


@pytest.mark.parametrize(
    "raw",
    [
        "!!! not base64 !!!",
        # Valid base64 whose bytes are not UTF-8.
        base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii").rstrip("="),
        # Shorter than the scope tag itself.
        _raw("abc"),
        # A well-formed-looking tag with no key after it.
        _raw("0" * SCOPE_DIGEST_CHARS),
        _raw("0" * SCOPE_DIGEST_CHARS + "   "),
    ],
)
def test_a_malformed_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(raw, scope=SOURCE_LIST_SCOPE)


@pytest.mark.parametrize("key", ["", "   "])
def test_a_correctly_scoped_cursor_naming_no_row_is_refused(key: str) -> None:
    """Past the scope check there is still nothing to resume from, so it is not a cursor."""
    with pytest.raises(ValueError, match="not a valid pagination cursor"):
        decode_cursor(encode_cursor(SOURCE_LIST_SCOPE, key), scope=SOURCE_LIST_SCOPE)


def test_surrounding_whitespace_is_trimmed_from_the_cursor_but_not_its_key() -> None:
    """Base64 has no meaningful surrounding whitespace; an identifier's whitespace is its own."""
    cursor = encode_cursor(SOURCE_LIST_SCOPE, " padded-id ")

    assert decode_cursor(f"  {cursor}  ", scope=SOURCE_LIST_SCOPE) == " padded-id "


def test_a_cursor_refusal_never_echoes_what_was_supplied() -> None:
    """Refusals name the rule. A cursor is caller-supplied text and is not repeated back."""
    secret = _raw("Interview with Alice")

    with pytest.raises(ValueError) as raised:
        decode_cursor(secret, scope=SOURCE_LIST_SCOPE)

    assert "Alice" not in str(raised.value)
    assert secret not in str(raised.value)
