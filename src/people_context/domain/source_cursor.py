"""Opaque bounded keyset cursors for source inspection.

Inspection pages a table that can grow without limit, so continuation is a *key*, not an offset:
each page resumes from the last row it returned, which keeps the read cost of page 500 identical
to the read cost of page one and never renumbers rows a concurrent staging run inserted.

The cursor is opaque on purpose. It encodes a position in one ordering, and a caller that decoded
it and started composing its own would be depending on a sort key that is free to change. Opaque
is not a protection, though: the encoding here is reversible, so a cursor still carries whatever
the key carries, which is why the keys are internal ids and timestamps and never a label, a name,
or anything else a caller authored.

Every value is bounded and validated *before* it can reach a query. Everything here raises plain
``ValueError``; the process boundary wraps it in the refusal its own callers expect.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Final

#: Characters an encoded cursor may carry. Generous enough for the keys below and small enough
#: that a hostile value costs a length check rather than a decode.
MAX_CURSOR_CHARS: Final = 512

#: Characters one decoded cursor may carry.
MAX_DECODED_CURSOR_CHARS: Final = 256

#: Characters an identifier inside a cursor may carry. Ids here are 26-character ULIDs; the
#: allowance leaves room for a restored id from another implementation without being unbounded.
MAX_CURSOR_ID_CHARS: Final = 64

#: A conservative identifier alphabet. A cursor id is compared against a primary key, never
#: rendered, so anything outside this set is refused rather than normalized.
CURSOR_ID_PATTERN: Final = re.compile(r"\A[A-Za-z0-9._:-]+\Z")

#: Separator inside a composed cursor. A unit separator occurs in neither an ISO-8601 timestamp
#: nor the identifier alphabet above, so the composition is unambiguous.
CURSOR_SEPARATOR: Final = "\x1f"


def encode_source_cursor(created_at: datetime, session_id: str) -> str:
    """Return the opaque cursor resuming a newest-first source listing after one row."""
    return _encode(f"{created_at.isoformat()}{CURSOR_SEPARATOR}{session_id}")


def decode_source_cursor(raw: str) -> tuple[datetime, str]:
    """Return the ``(created_at, id)`` key one source cursor names, or raise ``ValueError``.

    The timestamp round-trips through ``isoformat``/``fromisoformat`` because that is exactly how
    the stored column was written, so the decoded key re-renders to the same text the keyset
    predicate compares against.
    """
    decoded = _decode(raw)
    parts = decoded.split(CURSOR_SEPARATOR)
    if len(parts) != 2:
        raise ValueError("cursor is not a source listing position")
    raw_created_at, session_id = parts
    try:
        created_at = datetime.fromisoformat(raw_created_at)
    except ValueError:
        raise ValueError("cursor is not a source listing position") from None
    if created_at.tzinfo is None:
        raise ValueError("cursor is not a source listing position")
    return created_at, _checked_id(session_id)


def encode_mapping_cursor(candidate_id: str) -> str:
    """Return the opaque cursor resuming one source's mapping page after a candidate."""
    return _encode(candidate_id)


def decode_mapping_cursor(raw: str) -> str:
    """Return the candidate id one mapping cursor names, or raise ``ValueError``."""
    return _checked_id(_decode(raw))


def _encode(payload: str) -> str:
    """Return one cursor payload as unpadded URL-safe base64."""
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(raw: str) -> str:
    """Return one cursor's payload, refusing anything oversized or malformed.

    The length check runs first so an enormous value is refused without being decoded, and the
    decoded text is bounded again because base64 expands what it carries by a known factor only
    when the input is well formed.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cursor must not be blank")
    if len(text) > MAX_CURSOR_CHARS:
        raise ValueError(f"cursor is at most {MAX_CURSOR_CHARS} characters")
    padding = "=" * (-len(text) % 4)
    try:
        decoded = base64.urlsafe_b64decode(text + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("cursor is not a valid pagination cursor") from None
    if len(decoded) > MAX_DECODED_CURSOR_CHARS:
        raise ValueError("cursor is not a valid pagination cursor")
    return decoded


def _checked_id(value: str) -> str:
    """Return one accepted identifier from inside a cursor, or raise ``ValueError``."""
    if not value or len(value) > MAX_CURSOR_ID_CHARS or not CURSOR_ID_PATTERN.match(value):
        raise ValueError("cursor is not a valid pagination cursor")
    return value
