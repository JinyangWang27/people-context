"""Relationship vocabulary values and normalization helpers."""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field, model_validator

_RELATIONSHIP_TOKEN = re.compile(r"[^\w]+", re.UNICODE)


class RelationshipType(BaseModel):
    """One vocabulary row describing direction, inverse, and category."""

    type: str
    inverse: str | None = None
    symmetric: bool = False
    category: str
    canonical: bool = True
    synonyms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_direction(self) -> RelationshipType:
        if self.symmetric and self.inverse is not None:
            raise ValueError("symmetric relationship types cannot define an inverse")
        if not self.canonical and self.inverse is None:
            raise ValueError("non-canonical relationship types must name their canonical inverse")
        return self


class NormalizedRelationship(BaseModel):
    """Canonical storage coordinates derived from an input assertion."""

    subject_id: str
    object_id: str
    type: str
    category: str
    symmetric: bool = False


def normalize_relationship_type(value: str) -> str:
    """Normalize free-form relationship vocabulary to stable snake_case."""
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = _RELATIONSHIP_TOKEN.sub("_", text)
    return text.strip("_")
