"""Opaque bounded keyset cursors for source inspection.

Inspection pages tables that can grow without limit, so continuation is a *key*, not an offset:
each page resumes from the last row it returned, which keeps the read cost of page 500 identical
to the read cost of page one and never renumbers rows a concurrent staging run inserted.

Three rules shape the encoding.

**A cursor carries an identifier, never a sort key.** The encoding is reversible base64, so a
cursor discloses whatever it holds — and one of the rows it can end a page on is a terminal
`redacted` source, whose timestamps the hard-forget contract promises never to expose. A cursor
built from a sort key would have leaked exactly that through the one value a caller is handed and
told to pass back. So the key is the row's identifier, which inspection discloses for every source
including a redacted one, and the store resolves that identifier to its own sort position.

**A cursor names the listing that issued it.** Every cursor carries a scope, and a decode that
expects a different one refuses. Without that, a cursor from one source's mapping page would be
accepted by another source's as a bare `candidate_id >` boundary: the query would succeed and
silently omit the provenance sorting below it, which is a wrong answer presented as a complete
one. The scope is length-prefixed rather than delimited, so an identifier containing any byte at
all still parses back unambiguously.

**Identifiers are format-opaque and unbounded in shape.** A bootstrap bundle preserves ids verbatim
and requires only that they are non-blank, so inspection imposes no length or alphabet rule of its
own: one would make restored provenance visible but impossible to look up or page through. The only
limit is a resource bound on the encoded cursor this surface will parse at all, which is generous
enough that no identifier a real store holds comes close. Identifiers are never parsed or compared
by shape; every use is a bound SQL parameter.

Everything here raises plain ``ValueError``; the process boundary wraps it in the refusal its own
callers expect.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Final

#: Characters an encoded cursor may carry.
#:
#: This is the one bound, and it is a resource limit rather than a statement about identifiers: it
#: stops a hostile value at a length check instead of a decode. It allows roughly 1.5 KB of scope
#: plus identifier, which no id this project mints — 26-character ULIDs — approaches, and which
#: leaves ample room for the format-opaque ids a bundle from another implementation may carry.
MAX_CURSOR_CHARS: Final = 2048

#: Scope of a cursor issued by the source listing.
SOURCE_LIST_SCOPE: Final = "sources"

#: Digits of the length prefix that separates a cursor's scope from its key.
_LENGTH_PREFIX: Final = re.compile(r"\A([0-9]{1,4}):")


def mapping_scope(session_id: str) -> str:
    """Return the cursor scope naming one source's own mapping page."""
    return f"source:{session_id}"


def encode_cursor(scope: str, key: str) -> str:
    """Return the opaque cursor resuming `scope`'s page after the row with this key."""
    return base64.urlsafe_b64encode(f"{len(scope)}:{scope}{key}".encode()).decode("ascii").rstrip("=")


def decode_cursor(raw: str, *, scope: str) -> str:
    """Return the key one cursor names, or raise ``ValueError``.

    The length check runs before the decode so an enormous value is refused without being decoded.

    The key is returned exactly as it was encoded. Only the surrounding encoded text is trimmed,
    never the payload: ids are preserved verbatim across a bootstrap restore, so normalizing one
    here could stop it matching the row it names.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cursor must not be blank")
    if len(text) > MAX_CURSOR_CHARS:
        raise ValueError(f"cursor is at most {MAX_CURSOR_CHARS} characters")
    padding = "=" * (-len(text) % 4)
    try:
        payload = base64.urlsafe_b64decode(text + padding).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("cursor is not a valid pagination cursor") from None
    found_scope, key = _split(payload)
    if found_scope != scope:
        raise ValueError("cursor was issued for a different listing")
    if not key.strip():
        raise ValueError("cursor is not a valid pagination cursor")
    return key


def _split(payload: str) -> tuple[str, str]:
    """Split one decoded payload into its scope and key by the declared scope length.

    A length prefix rather than a delimiter, because both halves are format-opaque: a separator
    byte could legitimately occur inside a restored identifier and would then split the payload in
    the wrong place, silently addressing a different row.
    """
    match = _LENGTH_PREFIX.match(payload)
    if match is None:
        raise ValueError("cursor is not a valid pagination cursor")
    length = int(match.group(1))
    rest = payload[match.end() :]
    if length > len(rest):
        raise ValueError("cursor is not a valid pagination cursor")
    return rest[:length], rest[length:]
