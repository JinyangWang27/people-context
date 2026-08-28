"""A pagination cursor is bounded and validated before it can reach a query."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from people_context.domain.source_cursor import (
    MAX_CURSOR_CHARS,
    MAX_CURSOR_ID_CHARS,
    decode_mapping_cursor,
    decode_source_cursor,
    encode_mapping_cursor,
    encode_source_cursor,
)


def _cursor(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def test_source_cursor_round_trips_its_key_exactly() -> None:
    created_at = datetime(2026, 7, 20, 9, 30, 15, 123456, tzinfo=UTC)

    decoded = decode_source_cursor(encode_source_cursor(created_at, "01JZ0000000000000000000001"))

    assert decoded == (created_at, "01JZ0000000000000000000001")


def test_source_cursor_timestamp_re_renders_to_the_stored_text() -> None:
    """The keyset predicate compares ISO text, so the decoded key must produce the same bytes."""
    stored = datetime(2026, 7, 20, 9, 0, tzinfo=UTC).isoformat()

    created_at, _ = decode_source_cursor(
        encode_source_cursor(datetime.fromisoformat(stored), "01JZ0000000000000000000001")
    )

    assert created_at.isoformat() == stored


def test_mapping_cursor_round_trips_its_candidate_id() -> None:
    assert decode_mapping_cursor(encode_mapping_cursor("01JZ0000000000000000000009")) == (
        "01JZ0000000000000000000009"
    )


def test_an_oversized_cursor_is_refused_without_being_decoded() -> None:
    with pytest.raises(ValueError, match=f"at most {MAX_CURSOR_CHARS} characters"):
        decode_mapping_cursor("A" * (MAX_CURSOR_CHARS + 1))


def test_a_blank_cursor_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        decode_source_cursor("   ")


@pytest.mark.parametrize(
    "raw",
    [
        "!!! not base64 !!!",
        _cursor("no separator here"),
        _cursor("2026-07-20T09:00:00+00:00\x1f"),
        _cursor("not-a-timestamp\x1f01JZ0000000000000000000001"),
        # A naive timestamp has no instant, so it cannot address a row in a stored ordering.
        _cursor("2026-07-20T09:00:00\x1f01JZ0000000000000000000001"),
        # Three parts is not a key this surface ever issued.
        _cursor("2026-07-20T09:00:00+00:00\x1fid\x1fextra"),
    ],
)
def test_a_malformed_source_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_source_cursor(raw)


@pytest.mark.parametrize(
    "identifier",
    [
        "id with spaces",
        "id'; DROP TABLE import_staging; --",
        "A" * (MAX_CURSOR_ID_CHARS + 1),
    ],
)
def test_an_identifier_outside_the_cursor_alphabet_is_refused(identifier: str) -> None:
    with pytest.raises(ValueError, match="not a valid pagination cursor"):
        decode_mapping_cursor(_cursor(identifier))


def test_a_cursor_refusal_never_echoes_what_was_supplied() -> None:
    """Refusals name the rule. A cursor is caller-supplied text and is not repeated back."""
    secret = _cursor("Interview with Alice")

    with pytest.raises(ValueError) as raised:
        decode_source_cursor(secret)

    assert "Alice" not in str(raised.value)
    assert secret not in str(raised.value)
