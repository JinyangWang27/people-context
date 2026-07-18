"""Values used to export a durable relationship vault."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class VaultRelationship(BaseModel):
    """One relationship rendered from the exported person's perspective."""

    other_person_id: str
    display_type: str
    label: str | None = None


class VaultAffiliation(BaseModel):
    """One person-to-organization affiliation."""

    org_id: str
    org_name: str
    role: str
    valid_from: date | None = None
    valid_to: date | None = None


class VaultFact(BaseModel):
    """One durable fact allowed through the requested sensitivity gate."""

    predicate: str
    value: str
    valid_from: date | None = None
    valid_to: date | None = None


class VaultReminder(BaseModel):
    """One active reminder included in a person note."""

    text: str
    due_at: datetime | None = None


class VaultPerson(BaseModel):
    """Minimal person record needed to render one Obsidian note."""

    id: str
    name: str
    is_self: bool = False
    summary: str | None = None
    aliases: list[str] = Field(default_factory=list)
    relationships: list[VaultRelationship] = Field(default_factory=list)
    affiliations: list[VaultAffiliation] = Field(default_factory=list)
    facts: list[VaultFact] = Field(default_factory=list)
    reminders: list[VaultReminder] = Field(default_factory=list)


class VaultOrganizationMember(BaseModel):
    """One person linked from an organization hub note."""

    person_id: str
    person_name: str
    role: str
    valid_from: date | None = None
    valid_to: date | None = None


class VaultOrganization(BaseModel):
    """One organization hub represented in the vault graph."""

    id: str
    name: str
    kind: str | None = None
    members: list[VaultOrganizationMember] = Field(default_factory=list)


class VaultSnapshot(BaseModel):
    """Complete deterministic input to the filesystem vault adapter."""

    people: list[VaultPerson] = Field(default_factory=list)
    organizations: list[VaultOrganization] = Field(default_factory=list)
