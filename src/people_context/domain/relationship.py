"""Directed relationship edge between two persons."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Confidence, Provenance, ValidityPeriod, new_id, utc_now


class Relationship(BaseModel):
    """A directed subject -> object relationship with a validity period."""

    id: str = Field(default_factory=new_id)
    subject_id: str
    object_id: str
    type: str
    label: str | None = None
    period: ValidityPeriod = Field(default_factory=ValidityPeriod)
    confidence: Confidence = 1.0
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)
