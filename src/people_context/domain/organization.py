"""Organization entity and person-to-organization affiliations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Confidence, Provenance, ValidityPeriod, new_id, utc_now


class Organization(BaseModel):
    """An organization a person can be affiliated with."""

    id: str = Field(default_factory=new_id)
    name: str
    kind: str | None = None


class Affiliation(BaseModel):
    """A person's role at an organization over a validity period."""

    id: str = Field(default_factory=new_id)
    person_id: str
    org_id: str
    role: str
    period: ValidityPeriod = Field(default_factory=ValidityPeriod)
    confidence: Confidence = 1.0
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)
