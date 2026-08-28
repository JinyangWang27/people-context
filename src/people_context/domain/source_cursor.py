"""Opaque keyset cursors for source inspection.

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

**A cursor names the listing that issued it.** Every cursor opens with a fixed-width digest of its
scope, and a decode that expects a different scope refuses. Without that, a cursor from one
source's mapping page would be accepted by another source's as a bare `candidate_id >` boundary:
the query would succeed and silently omit the provenance sorting below it, which is a wrong answer
presented as a complete page. The digest is a mix-up guard rather than a security boundary — the
scope it stands for is derived from arguments the caller already supplies — and being fixed-width
it both parses unambiguously against any identifier content and keeps a cursor's size independent
of how long the scoping identifier happens to be.

**Nothing here bounds an identifier.** A bootstrap bundle preserves ids verbatim and requires only
that they are non-blank, so any ceiling — on the id, or on the cursor that must round-trip it —
eventually refuses a cursor this same surface issued, leaving a restored database's provenance
visible but impossible to page through. There is deliberately no such ceiling: a cursor arrives
already materialized as one argument, base64 shrinks rather than amplifies what it decodes, and
the key is only ever a bound SQL parameter of an exact comparison.

Everything here raises plain ``ValueError``; the process boundary wraps it in the refusal its own
callers expect.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Final

#: Scope of a cursor issued by the source listing.
SOURCE_LIST_SCOPE: Final = "sources"

#: Hexadecimal characters of the scope digest that opens every cursor.
#:
#: Short because it identifies which of a handful of listings issued a cursor, not who did. A
#: collision would at worst let one source's cursor page another's, which is the same mistake this
#: guards against being caught 2**64 times less often — not an escalation.
SCOPE_DIGEST_CHARS: Final = 16


def mapping_scope(session_id: str) -> str:
    """Return the cursor scope naming one source's own mapping page."""
    return f"source:{session_id}"


def encode_cursor(scope: str, key: str) -> str:
    """Return the opaque cursor resuming `scope`'s page after the row with this key."""
    return _b64(f"{_scope_digest(scope)}{key}")


def decode_cursor(raw: str, *, scope: str) -> str:
    """Return the key one cursor names, or raise ``ValueError``.

    The key is returned exactly as it was encoded. Only the surrounding encoded text is trimmed,
    never the payload: ids are preserved verbatim across a bootstrap restore, so normalizing one
    here could stop it matching the row it names.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cursor must not be blank")
    padding = "=" * (-len(text) % 4)
    try:
        payload = _b64decode(text + padding)
    except ValueError:
        raise ValueError("cursor is not a valid pagination cursor") from None
    if len(payload) < SCOPE_DIGEST_CHARS:
        raise ValueError("cursor is not a valid pagination cursor")
    found, key = payload[:SCOPE_DIGEST_CHARS], payload[SCOPE_DIGEST_CHARS:]
    if not hmac.compare_digest(found, _scope_digest(scope)):
        raise ValueError("cursor was issued for a different listing")
    if not key.strip():
        raise ValueError("cursor is not a valid pagination cursor")
    return key


def _scope_digest(scope: str) -> str:
    """Return the fixed-width tag standing for one listing."""
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:SCOPE_DIGEST_CHARS]


def _b64(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(text: str) -> str:
    try:
        return base64.urlsafe_b64decode(text).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("cursor is not a valid pagination cursor") from exc
