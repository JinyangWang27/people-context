"""Assertive record, affiliation, correction, and reminder use cases."""

from people_context.app._mutation import (
    InvalidCorrectionError,
    InvalidReminderError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
    ReminderNotActiveError,
)
from people_context.app.records.affiliations import SetAffiliation, SetAffiliationInput
from people_context.app.records.corrections import CorrectRecord, CorrectRecordInput
from people_context.app.records.doctor import (
    DOCTOR_FORMAT,
    DOCTOR_VERSION,
    FINDING_CODES,
    CliAction,
    DoctorError,
    DoctorFinding,
    DoctorReport,
    FactEvidence,
    FindingCode,
    FindingPerson,
    McpAction,
    NameEvidence,
    ReferenceEvidence,
    ReportDoctorFindings,
    render_doctor_json,
)
from people_context.app.records.facts import RecordFact, RecordFactInput
from people_context.app.records.interactions import RecordInteraction, RecordInteractionInput
from people_context.app.records.observations import RecordObservation, RecordObservationInput
from people_context.app.records.reminders import (
    CompleteReminder,
    CompleteReminderInput,
    ListReminders,
    ListRemindersInput,
    SetReminder,
    SetReminderInput,
)
from people_context.app.records.traits import RecordTrait, RecordTraitInput

__all__ = [
    "DOCTOR_FORMAT",
    "DOCTOR_VERSION",
    "FINDING_CODES",
    "CliAction",
    "CompleteReminder",
    "CompleteReminderInput",
    "CorrectRecord",
    "CorrectRecordInput",
    "DoctorError",
    "DoctorFinding",
    "DoctorReport",
    "FactEvidence",
    "FindingCode",
    "FindingPerson",
    "InvalidCorrectionError",
    "InvalidReminderError",
    "ListReminders",
    "ListRemindersInput",
    "McpAction",
    "NameEvidence",
    "OrganizationNotFoundError",
    "PersonNotFoundError",
    "RecordFact",
    "RecordFactInput",
    "RecordInteraction",
    "RecordInteractionInput",
    "RecordNotFoundError",
    "RecordObservation",
    "RecordObservationInput",
    "RecordTrait",
    "RecordTraitInput",
    "ReferenceEvidence",
    "ReminderNotActiveError",
    "ReportDoctorFindings",
    "SetAffiliation",
    "SetAffiliationInput",
    "SetReminder",
    "SetReminderInput",
    "render_doctor_json",
]
