"""Shared public models for hydrated relationship, affiliation, and education context."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.domain.education import Education
from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.ports.context import AffiliationRecord, EducationRecord, RelationshipRecord


class PersonRelationshipContext(BaseModel):
    """A relationship plus the other endpoint and perspective-rendered type."""

    relationship: Relationship
    other_person_id: str
    other_person_name: str
    display_type: str


class PersonAffiliationContext(BaseModel):
    """An affiliation plus its organization's display name."""

    affiliation: Affiliation
    organization_name: str


class PersonEducationContext(BaseModel):
    """An education row plus its institution's display name."""

    education: Education
    institution_name: str


def relationship_context(record: RelationshipRecord) -> PersonRelationshipContext:
    """Convert a context-port relationship record to the public app model."""
    return PersonRelationshipContext(
        relationship=record.relationship,
        other_person_id=record.other_person_id,
        other_person_name=record.other_person_name,
        display_type=record.display_type or record.relationship.type,
    )


def affiliation_context(record: AffiliationRecord) -> PersonAffiliationContext:
    """Convert a context-port affiliation record to the public app model."""
    return PersonAffiliationContext(affiliation=record.affiliation, organization_name=record.organization_name)


def education_context(record: EducationRecord) -> PersonEducationContext:
    """Convert a context-port education record to the public app model."""
    return PersonEducationContext(education=record.education, institution_name=record.institution_name)
