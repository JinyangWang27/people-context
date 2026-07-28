"""Relationship vocabulary values and normalization helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

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


_SEEDED_TYPES: tuple[RelationshipType, ...] = (
    RelationshipType(type="acquaintance_of", symmetric=True, category="social", synonyms=["acquaintance"]),
    RelationshipType(type="child_of", inverse="parent_of", canonical=False, category="family", synonyms=["child"]),
    RelationshipType(type="colleague_of", symmetric=True, category="professional", synonyms=["colleague", "coworker"]),
    RelationshipType(type="cousin_of", symmetric=True, category="family", synonyms=["cousin"]),
    RelationshipType(type="friend_of", symmetric=True, category="social", synonyms=["friend", "friend_of"]),
    RelationshipType(
        type="manages",
        inverse="reports_to",
        canonical=False,
        category="professional",
        synonyms=["manager_of", "manages"],
    ),
    RelationshipType(
        type="mentee_of",
        inverse="mentor_of",
        canonical=False,
        category="professional",
        synonyms=["mentee"],
    ),
    RelationshipType(type="mentor_of", inverse="mentee_of", category="professional", synonyms=["mentor", "mentors"]),
    RelationshipType(type="neighbor_of", symmetric=True, category="social", synonyms=["neighbor", "neighbour"]),
    RelationshipType(type="parent_of", inverse="child_of", category="family", synonyms=["parent"]),
    RelationshipType(type="partner_of", symmetric=True, category="family", synonyms=["partner"]),
    RelationshipType(
        type="reports_to",
        inverse="manages",
        category="professional",
        synonyms=["reports_to", "reports_to_manager"],
    ),
    RelationshipType(type="sibling_of", symmetric=True, category="family", synonyms=["sibling"]),
    RelationshipType(type="spouse_of", symmetric=True, category="family", synonyms=["spouse"]),
)

#: The canonical reference vocabulary seeded by the relationship-vocabulary migration.
#:
#: Bootstrap restore compares a destination against this definition to decide whether the
#: target still carries only seeded reference rows, and treats these types as resolvable
#: even when a bundle omits them. A real database is the authority on the seeded rows; the
#: adapter test suite asserts this mapping stays byte-identical to a freshly migrated store.
SEEDED_RELATIONSHIP_TYPES: Mapping[str, RelationshipType] = MappingProxyType(
    {row.type: row for row in _SEEDED_TYPES}
)

#: Seeded synonym rows, keyed by synonym, in the same authority relationship as the types above.
SEEDED_RELATIONSHIP_SYNONYMS: Mapping[str, str] = MappingProxyType(
    {synonym: row.type for row in _SEEDED_TYPES for synonym in row.synonyms}
)
