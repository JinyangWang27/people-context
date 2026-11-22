"""Record a person's study at an institution."""

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
from people_context.domain.education import Education, EducationLabel
from people_context.domain.shared import Confidence, ValidityPeriod, new_id
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import OrganizationStore, RecordWriter
from people_context.ports.repository import PersonReader


class RecordEducationInput(BaseModel):
    """Input for one person-to-institution study assertion."""

    person_id: str
    institution: str
    degree: EducationLabel | None = None
    field_of_study: EducationLabel | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    confidence: Confidence | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class RecordEducation:
    """Resolve the institution like any organization and record the study against it."""

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
    def execute(self, data: RecordEducationInput, *, transaction_id: str | None = None) -> Education:
        """Create and audit an education record, auditing institution creation separately.

        An exact repeat — same person, institution, degree, field, and period — returns the stored
        row and writes nothing. A second degree at the same institution is a second row.
        """
        require_active_person(self._people, data.person_id)
        period = ValidityPeriod(valid_from=data.valid_from, valid_to=data.valid_to)
        transaction_id = transaction_id or new_id()
        institution = resolve_organization(
            self._organizations,
            self._audit,
            self._clock,
            data.institution,
            source=data.source,
            transaction_id=transaction_id,
            session=data.session,
            stated_by=data.stated_by,
        )
        existing = self._writer.find_education(data.person_id, institution.id, data.degree, data.field_of_study, period)
        if existing is not None:
            return existing
        education = Education(
            person_id=data.person_id,
            org_id=institution.id,
            degree=data.degree,
            field_of_study=data.field_of_study,
            period=period,
            confidence=data.confidence if data.confidence is not None else 1.0,
            provenance=provenance(data.source, data.session, data.stated_by),
            created_at=self._clock.now(),
        )
        self._writer.save_education(education)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type="education",
            entity_id=education.id,
            payload=snapshot(education),
            source=data.source,
            transaction_id=transaction_id,
            session=data.session,
            stated_by=data.stated_by,
        )
        return education
