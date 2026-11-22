"""Create an affiliation with organization get-or-create semantics."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from people_context.app._mutation import (
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.app.records.organizations import resolve_organization
from people_context.domain.organization import Affiliation
from people_context.domain.shared import Confidence, ValidityPeriod, new_id
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import OrganizationStore, RecordWriter
from people_context.ports.repository import PersonReader


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
    def execute(self, data: SetAffiliationInput, *, transaction_id: str | None = None) -> Affiliation:
        """Create and audit an affiliation, auditing organization creation separately.

        An exact repeat — same person, organization, role, and validity period — returns the stored row
        and writes nothing: it is the same claim, not a second role. A different role still adds a row.
        """
        require_active_person(self._people, data.person_id)
        transaction_id = transaction_id or new_id()
        organization = resolve_organization(
            self._organizations,
            self._audit,
            self._clock,
            data.org,
            source=data.source,
            transaction_id=transaction_id,
            session=data.session,
            stated_by=data.stated_by,
        )
        period = ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to)
        existing = self._writer.find_affiliation(data.person_id, organization.id, data.role, period)
        if existing is not None:
            return existing
        affiliation = Affiliation(
            person_id=data.person_id,
            org_id=organization.id,
            role=data.role,
            period=period,
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
