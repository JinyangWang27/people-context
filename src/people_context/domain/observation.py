"""Subjective observation about a person, kept distinct from facts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Provenance, Sensitivity, new_id, utc_now


class Observation(BaseModel):
    """An explicitly subjective note about a person."""

    id: str = Field(default_factory=new_id)
    person_id: str
    text: str
    observed_at: datetime = Field(default_factory=utc_now)
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
