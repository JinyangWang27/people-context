"""Person entity and its name aliases."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id, utc_now


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
