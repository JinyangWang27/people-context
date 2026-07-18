"""Create an affiliation with organization get-or-create semantics."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

from people_context.app.write_support import (
    OrganizationNotFoundError,
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.shared import Confidence, ValidityPeriod, new_id, normalize_name
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import OrganizationStore, RecordWriter
from people_context.ports.repository import PersonReader

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE)


class SetAffiliationInput(BaseModel):
    """Input for a person-to-organization role assertion."""

    person_id: str
    org: str
    role: str
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SetAffiliation:
    """Resolve an organization id or get/create one by normalized name."""

    def __init__(
        self,
        people: PersonReader,
        organizations: OrganizationStore,
        writer: RecordWriter,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._people = people
        self._organizations = organizations
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)

    @transactional
    def execute(self, data: SetAffiliationInput) -> Affiliation:
        """Create and audit an affiliation, auditing organization creation separately."""
        require_active_person(self._people, data.person_id)
        transaction_id = new_id()
        organization = self._organizations.get(data.org)
        if organization is None and _ULID_PATTERN.fullmatch(data.org):
            raise OrganizationNotFoundError(data.org)
        if organization is None:
            organization = self._organizations.get_by_normalized_name(normalize_name(data.org))
        if organization is None:
            organization = Organization(name=data.org)
            self._organizations.save(organization)
            audit_mutation(
                self._audit,
                self._clock,
                op="create",
                entity_type="organization",
                entity_id=organization.id,
                payload=snapshot(organization),
                source=data.source,
                transaction_id=transaction_id,
                session=data.session,
                stated_by=data.stated_by,
            )
        affiliation = Affiliation(
            person_id=data.person_id,
            org_id=organization.id,
            role=data.role,
            period=ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to),
            confidence=data.confidence if data.confidence is not None else 1.0,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._writer.save_affiliation(affiliation)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="affiliation",
            entity_id=affiliation.id,
            payload=snapshot(affiliation),
            source=data.source,
            transaction_id=transaction_id,
            session=data.session,
            stated_by=data.stated_by,
        )
        return affiliation
