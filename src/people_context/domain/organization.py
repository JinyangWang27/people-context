"""Organization entity and person-to-organization affiliations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Confidence, Provenance, new_id, utc_now
from people_context.domain.tenure import TenurePeriod


class Organization(BaseModel):
    """An organization a person can be affiliated with."""

    id: str = Field(default_factory=new_id)
    name: str
    kind: str | None = None
    #: What the organization does, e.g. "telecommunications". A property of the organization, so it
    #: lives here rather than on each person's affiliation with it.
    industry: str | None = None


class Affiliation(BaseModel):
    """A person's role at an organization over a period known to a day, a month, or a year."""

    id: str = Field(default_factory=new_id)
    person_id: str
    org_id: str
    role: str
    period: TenurePeriod = Field(default_factory=TenurePeriod)
    confidence: Confidence = 1.0
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)
