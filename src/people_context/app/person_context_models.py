"""Shared public models for hydrated relationship and affiliation context."""

from __future__ import annotations

from pydantic import BaseModel

from people_context.domain.organization import Affiliation
from people_context.domain.relationship import Relationship
from people_context.ports.context import AffiliationRecord, RelationshipRecord


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
