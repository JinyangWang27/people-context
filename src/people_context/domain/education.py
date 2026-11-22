"""A person's study at an institution, kept apart from employment.

Education is not a kind of affiliation. Every reader of `Affiliation` takes it to mean a job —
the vCard export writes the active one as `ORG`/`TITLE`, guidance reads it as where someone works
— so a degree stored there would surface as an employer. It is its own record with its own fields
instead, and the institution is an ordinary `Organization`.

Like an affiliation, it carries no sensitivity level: it is disclosed by every ordinary read. A
private detail about someone's studies belongs in a fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final

from pydantic import BaseModel, Field, StringConstraints

from people_context.domain.shared import Confidence, Provenance, new_id, utc_now
from people_context.domain.tenure import TenurePeriod

#: Characters a degree or field of study may carry. Each names one thing — "MSc", "Cognitive
#: Science" — so a longer value is a pasted passage, which belongs nowhere in a structured record.
MAX_EDUCATION_LABEL_CHARS: Final = 200

EducationLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EDUCATION_LABEL_CHARS)
]


class Education(BaseModel):
    """One assertion that a person studied at an institution over a validity period.

    `degree` and `field_of_study` are separate so "who has a PhD" and "who studied Computer Science"
    are both answerable. Either may be unknown; an absent value is the absence of a claim.
    """

    id: str = Field(default_factory=new_id)
    person_id: str
    org_id: str
    degree: EducationLabel | None = None
    field_of_study: EducationLabel | None = None
    period: TenurePeriod = Field(default_factory=TenurePeriod)
    confidence: Confidence = 1.0
    provenance: Provenance
    created_at: datetime = Field(default_factory=utc_now)
