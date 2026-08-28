"""Opaque bounded keyset cursors for source inspection.

Inspection pages tables that can grow without limit, so continuation is a *key*, not an offset:
each page resumes from the last row it returned, which keeps the read cost of page 500 identical
to the read cost of page one and never renumbers rows a concurrent staging run inserted.

**A cursor carries an identifier and nothing else.** The encoding here is reversible base64, so a
cursor discloses whatever it holds — and one of the rows it can end a page on is a terminal
`redacted` source, whose timestamps the hard-forget contract promises never to expose. A cursor
built from a sort key would have leaked exactly that through the one field a caller is handed and
told to pass back. So the key is the row's identifier, which inspection discloses for every source
including a redacted one, and the store resolves that identifier to its own sort position.

The cursor is still opaque to the caller: it encodes a position in an ordering this project is
free to extend, and a caller that decoded one and started composing its own would be depending on
a sort key that is not a contract.

Identifiers are **format-opaque**. A restored bundle preserves ids verbatim and requires only that
they are non-blank, so anything narrower here — a ULID shape, an ASCII alphabet — would make
restored provenance impossible to page through. They are bounded and never interpreted; every use
is a bound SQL parameter.

Everything here raises plain ``ValueError``; the process boundary wraps it in the refusal its own
callers expect.
"""

from __future__ import annotations

import base64
import binascii
from typing import Final

#: Characters an encoded cursor may carry. Large enough for any identifier below once base64
#: expansion is allowed for, and small enough that a hostile value costs a length check rather
#: than a decode.
MAX_CURSOR_CHARS: Final = 512

#: Characters an identifier inside a cursor may carry.
#:
#: This matches the bound the rest of the project puts on an opaque caller-supplied identifier.
#: It is deliberately far wider than the 26-character ULIDs this installation mints: a bootstrap
#: bundle preserves ids exactly and validates them only as non-blank, so a database restored from
#: another implementation can legitimately hold longer ones, and refusing those would leave their
#: provenance visible but untraversable.
MAX_CURSOR_ID_CHARS: Final = 256


def encode_cursor(identifier: str) -> str:
    """Return the opaque cursor resuming a page after the row with this identifier."""
    return base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> str:
    """Return the identifier one cursor names, or raise ``ValueError``.

    The length check runs before the decode so an enormous value is refused without being
    decoded, and the decoded identifier is bounded again because base64 expands what it carries
    by a known factor only when the input is well formed.

    The identifier is returned exactly as it was encoded. Only the surrounding encoded text is
    trimmed, never the payload: ids are preserved verbatim across a bootstrap restore, so
    normalizing one here could stop it matching the row it names.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cursor must not be blank")
    if len(text) > MAX_CURSOR_CHARS:
        raise ValueError(f"cursor is at most {MAX_CURSOR_CHARS} characters")
    padding = "=" * (-len(text) % 4)
    try:
        identifier = base64.urlsafe_b64decode(text + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("cursor is not a valid pagination cursor") from None
    return check_cursor_identifier(identifier)


def check_cursor_identifier(value: str) -> str:
    """Return one accepted format-opaque identifier, or raise ``ValueError``.

    Bounded and non-blank is the whole rule. The value is never parsed, compared by shape, or
    interpolated — it reaches SQLite only as a bound parameter — so a narrower alphabet would
    refuse legitimate restored ids without buying anything.
    """
    if not value.strip():
        raise ValueError("cursor is not a valid pagination cursor")
    if len(value) > MAX_CURSOR_ID_CHARS:
        raise ValueError("cursor is not a valid pagination cursor")
    return value
