"""Shared value normalization for file-based import extractors."""

from __future__ import annotations

import re
import unicodedata

_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
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
