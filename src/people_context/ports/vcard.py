"""Typed vCard export projection and the writer port.

The projection is the whole contract between the application and the serializer: it is
already filtered, already gated, and already ordered, so the adapter never decides what
may leave the store and never reads a record the use case chose to exclude.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

VCARD_3_0 = "3.0"
VCARD_4_0 = "4.0"
# The two dialects the bundled importer accepts; export deliberately writes no other one.
SUPPORTED_VCARD_VERSIONS: tuple[str, ...] = (VCARD_3_0, VCARD_4_0)


@dataclass(frozen=True)
class VCardAffiliation:
    """One organization/role pair, kept together because the importer needs both."""

    organization: str
    role: str


@dataclass(frozen=True)
class VCardContact:
    """One person's selected vCard fields, with every optional value already resolved."""

    person_id: str
    full_name: str
    nicknames: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    affiliation: VCardAffiliation | None = None
    birthday: date | None = None


@dataclass(frozen=True)
class VCardProjection:
    """The complete ordered export plus the dialect it must be serialized in."""

    version: str
    contacts: tuple[VCardContact, ...] = ()


@runtime_checkable
class VCardWriter(Protocol):
    """Serialize an already-filtered projection into canonical vCard text."""

    def write_vcards(self, projection: VCardProjection) -> str: ...
