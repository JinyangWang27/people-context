"""Derived characteristic (trait) about a person."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from people_context.domain.shared import Confidence, Provenance, Sensitivity, new_id, utc_now


class TraitCategory(StrEnum):
    """Category of a derived characteristic."""

    COMMUNICATION_STYLE = "communication_style"
    TEMPERAMENT = "temperament"
    VALUES = "values"
    PREFERENCE = "preference"
    TOPICS_TO_AVOID = "topics_to_avoid"
    OTHER = "other"


class Trait(BaseModel):
    """A derived, subjective characteristic distilled from observations/interactions."""

    id: str = Field(default_factory=new_id)
    person_id: str
    category: TraitCategory
    value: str
    evidence_note: str | None = None
    confidence: Confidence = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
    updated_at: datetime = Field(default_factory=utc_now)
