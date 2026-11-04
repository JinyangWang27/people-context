"""Person entity and its name aliases."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_core import PydanticCustomError

from people_context.domain.shared import new_id, utc_now

_EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

#: Refusal code for an email-shaped alias of any kind but `handle`.
ALIAS_EMAIL_NOT_HANDLE = "alias_email_not_handle"


def is_email_shaped(value: str) -> bool:
    """Whether an alias value is an email address; `@handle` and ordinary names are not."""
    return _EMAIL_SHAPE.fullmatch(value.strip()) is not None


def check_alias_kind(value: str, kind: AliasKind) -> None:
    """Refuse an email address offered as a name: it is an alias only as a `handle`.

    Handles are how imports match identities, so an address belongs there. Applied to alias inputs
    only, never to the stored `Alias`, so people saved before this rule still load and can be fixed.
    """
    if kind is not AliasKind.HANDLE and is_email_shaped(value):
        raise PydanticCustomError(ALIAS_EMAIL_NOT_HANDLE, "an email address is an alias only with kind `handle`")


def kind_for_name(name: str, default: AliasKind) -> AliasKind:
    """The kind a name gets when it becomes an alias: an email address is always a `handle`."""
    return AliasKind.HANDLE if is_email_shaped(name) else default


class AliasKind(StrEnum):
    """Category of an alternate name for a person."""

    NICKNAME = "nickname"
    NATIVE_SCRIPT = "native_script"
    TRANSLITERATION = "transliteration"
    HANDLE = "handle"
    FORMER_NAME = "former_name"
    OTHER = "other"


class Alias(BaseModel):
    """An alternate name a person is known by."""

    id: str = Field(default_factory=new_id)
    value: str
    kind: AliasKind = AliasKind.OTHER
    lang: str | None = None
    script: str | None = None


class Person(BaseModel):
    """A person known to the system (including the user, via `is_self`)."""

    id: str = Field(default_factory=new_id)
    canonical_name: str
    is_self: bool = False
    summary: str | None = None
    aliases: list[Alias] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = None

    def all_names(self) -> list[str]:
        """Return the canonical name plus alias values, deduped, order-preserving."""
        names: list[str] = []
        seen: set[str] = set()
        for name in (self.canonical_name, *(alias.value for alias in self.aliases)):
            if name not in seen:
                seen.add(name)
                names.append(name)
        return names
