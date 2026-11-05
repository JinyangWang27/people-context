"""Shared value normalization for file-based import extractors."""

from __future__ import annotations

import re
import unicodedata

from people_context.domain.person import is_email_shaped

# An unquoted local part is dot-atom: non-empty atoms joined by single dots, so it can neither
# start nor end with a dot nor carry consecutive dots.
_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def normalize_email(value: str) -> str | None:
    """Return the lowercased ASCII address, or ``None`` when the value is not one.

    Case and compatibility forms are folded, but the address characters themselves are preserved:
    unlike name matching, an address must never have its combining marks stripped, because that
    would silently rewrite an internationalized address such as ``josé@example.com`` into a
    different, genuinely distinct ASCII address. Addresses outside the supported ASCII form are
    reported as invalid rather than rewritten.
    """
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return normalized if _EMAIL_RE.fullmatch(normalized) else None


def clean_text(value: object | None) -> str:
    """Collapse whitespace in a string cell and treat every other value as empty."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def person_name(value: str) -> str:
    """Return an extracted name, or the local part when the source put an email address there.

    Calendars and mail clients often repeat the address as the display name. An address is contact
    information, refused as a person's name, so one such field must not refuse the whole import.
    """
    return value.split("@", maxsplit=1)[0] if is_email_shaped(value) else value
