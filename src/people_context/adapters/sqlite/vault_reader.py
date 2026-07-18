"""SQLite projection for the CLI-only Obsidian vault export."""

from __future__ import annotations

import sqlite3
from datetime import date

from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.domain.shared import Sensitivity
from people_context.domain.vault import (
    VaultAffiliation,
    VaultFact,
    VaultOrganization,
    VaultOrganizationMember,
    VaultPerson,
    VaultRelationship,
    VaultReminder,
    VaultSnapshot,
)

_EXCLUDED_BY_DEFAULT = {Sensitivity.SENSITIVE, Sensitivity.RESTRICTED}


class SqliteVaultReader:
    """Read active people, structure, durable facts, and active reminders only."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._people = SqlitePeopleRepository(conn)
        self._context = SqliteContextReader(conn)

    def read_vault(self, *, include_sensitive: bool = False) -> VaultSnapshot:
        as_of = date.today()
        people: list[VaultPerson] = []
        organizations: dict[str, VaultOrganization] = {}
        for person in self._people.list_people():
            relationships = [
                VaultRelationship(
                    other_person_id=record.other_person_id,
                    display_type=record.display_type or record.relationship.type,
                    label=record.relationship.label,
                )
                for record in self._context.list_active_relationships(person.id, as_of)
            ]
            relationships.sort(key=lambda row: (row.display_type, row.other_person_id, row.label or ""))
            affiliations = [
                VaultAffiliation(
                    org_id=record.affiliation.org_id,
                    org_name=record.organization_name,
                    role=record.affiliation.role,
                    valid_from=record.affiliation.period.valid_from,
                    valid_to=record.affiliation.period.valid_to,
                )
                for record in self._context.list_active_affiliations(person.id, as_of)
            ]
            affiliations.sort(key=lambda row: (row.org_name.casefold(), row.role, row.org_id))
            for affiliation in affiliations:
                organization = organizations.get(affiliation.org_id)
                if organization is None:
                    row = self._conn.execute(
                        "SELECT id, name, kind FROM organizations WHERE id = ?",
                        (affiliation.org_id,),
                    ).fetchone()
                    organization = VaultOrganization(
                        id=affiliation.org_id,
                        name=row["name"] if row is not None else affiliation.org_name,
                        kind=row["kind"] if row is not None else None,
                    )
                    organizations[organization.id] = organization
                organization.members.append(
                    VaultOrganizationMember(
                        person_id=person.id,
                        person_name=person.canonical_name,
                        role=affiliation.role,
                        valid_from=affiliation.valid_from,
                        valid_to=affiliation.valid_to,
                    )
                )
            facts = [
                VaultFact(
                    predicate=fact.predicate,
                    value=fact.value,
                    valid_from=fact.period.valid_from,
                    valid_to=fact.period.valid_to,
                )
                for fact in self._context.list_facts(person.id)
                if include_sensitive or fact.sensitivity not in _EXCLUDED_BY_DEFAULT
            ]
            facts.sort(key=lambda row: (row.predicate.casefold(), row.value, row.valid_from or date.min))
            reminders = [
                VaultReminder(text=reminder.text, due_at=reminder.due_at)
                for reminder in self._context.list_active_reminders(person.id)
            ]
            reminders.sort(key=lambda row: (row.due_at is None, row.due_at, row.text))
            people.append(
                VaultPerson(
                    id=person.id,
                    name=person.canonical_name,
                    is_self=person.is_self,
                    summary=person.summary,
                    aliases=sorted({alias.value for alias in person.aliases}, key=str.casefold),
                    relationships=relationships,
                    affiliations=affiliations,
                    facts=facts,
                    reminders=reminders,
                )
            )
        people.sort(key=lambda row: (row.name.casefold(), row.id))
        organization_rows = list(organizations.values())
        for organization in organization_rows:
            organization.members.sort(key=lambda row: (row.person_name.casefold(), row.role, row.person_id))
        organization_rows.sort(key=lambda row: (row.name.casefold(), row.id))
        return VaultSnapshot(people=people, organizations=organization_rows)
