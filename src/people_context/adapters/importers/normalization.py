"""Shared value normalization for file-based import extractors."""

from __future__ import annotations

import re

from people_context.domain.shared import normalize_name

_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def normalize_email(value: str) -> str | None:
    """Return the normalized address, or ``None`` when the value is blank or not an address."""
    if not value:
        return None
    normalized = normalize_name(value)
    return normalized if _EMAIL_RE.fullmatch(normalized) else None


def clean_text(value: object | None) -> str:
    """Collapse whitespace in a string cell and treat every other value as empty."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())
