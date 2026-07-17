"""Time-aware factual record about a person."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Confidence, Provenance, Sensitivity, ValidityPeriod, new_id, utc_now


class Fact(BaseModel):
    """A bitemporal-lite factual assertion: validity period plus recording time."""

    id: str = Field(default_factory=new_id)
    person_id: str
    predicate: str
    value: str
    period: ValidityPeriod = Field(default_factory=ValidityPeriod)
    recorded_at: datetime = Field(default_factory=utc_now)
    confidence: Confidence = 1.0
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
