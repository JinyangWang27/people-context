"""Application layer: use cases orchestrating domain entities over the ports."""

from __future__ import annotations

from people_context.app.add_alias import AddAlias, AddAliasInput
from people_context.app.complete_reminder import CompleteReminder, CompleteReminderInput
from people_context.app.correct_record import CorrectRecord, CorrectRecordInput
from people_context.app.edit_person import EditPerson, EditPersonInput, PersonNameCollisionError
from people_context.app.export_data import ExportData, ExportDocument
from people_context.app.forget import Forget, ForgetError, ForgetPreview, ForgetResult, PreviewForget
from people_context.app.get_communication_guidance import CommunicationGuidanceResult, GetCommunicationGuidance
from people_context.app.get_person_context import (
    GetPersonContext,
    PersonAffiliationContext,
    PersonContextResult,
    PersonIdentity,
    PersonRelationshipContext,
)
from people_context.app.import_content import (
    CommitImport,
    CommitImportResult,
    ImportBatchResult,
    ImportContent,
    ImportPipelineError,
    ImportReviewResult,
    ImportReviewRow,
    ReviewImport,
)
from people_context.app.list_reminders import ListReminders, ListRemindersInput
from people_context.app.merge_people import MergeMovedCounts, MergePeople, MergePeopleError, MergePeopleResult
from people_context.app.record import (
    AliasInput,
    AmbiguousPersonError,
    RememberPerson,
    RememberPersonInput,
    RememberPersonResult,
    SelfAlreadyExistsError,
)
from people_context.app.record_fact import RecordFact, RecordFactInput
from people_context.app.record_interaction import RecordInteraction, RecordInteractionInput
from people_context.app.record_observation import RecordObservation, RecordObservationInput
from people_context.app.record_trait import RecordTrait, RecordTraitInput
from people_context.app.reindex_people import ReindexPeople, ReindexPeopleResult
from people_context.app.resolve_person import ResolutionCandidate, ResolutionHints, ResolutionResult, ResolvePerson
from people_context.app.search_people import SearchPeople
from people_context.app.set_affiliation import SetAffiliation, SetAffiliationInput
from people_context.app.set_communication_philosophy import (
    SetCommunicationPhilosophy,
    SetCommunicationPhilosophyInput,
)
from people_context.app.set_relationship import SetRelationship, SetRelationshipInput
from people_context.app.set_reminder import SetReminder, SetReminderInput
from people_context.app.write_support import (
    InvalidCorrectionError,
    InvalidReminderError,
    OrganizationNotFoundError,
    PersonNotFoundError,
    RecordNotFoundError,
    ReminderNotActiveError,
)

__all__ = [
    "AliasInput",
    "AddAlias",
    "AddAliasInput",
    "AmbiguousPersonError",
    "GetPersonContext",
    "CommunicationGuidanceResult",
    "GetCommunicationGuidance",
    "Forget",
    "ForgetError",
    "ForgetPreview",
    "ForgetResult",
    "ImportBatchResult",
    "ImportContent",
    "ImportPipelineError",
    "ImportReviewResult",
    "ImportReviewRow",
    "ListReminders",
    "ListRemindersInput",
    "MergeMovedCounts",
    "MergePeople",
    "MergePeopleError",
    "MergePeopleResult",
    "CompleteReminder",
    "CompleteReminderInput",
    "CommitImport",
    "CommitImportResult",
    "CorrectRecord",
    "CorrectRecordInput",
    "EditPerson",
    "EditPersonInput",
    "ExportData",
    "ExportDocument",
    "InvalidCorrectionError",
    "InvalidReminderError",
    "OrganizationNotFoundError",
    "PersonAffiliationContext",
    "PersonContextResult",
    "PersonIdentity",
    "PersonRelationshipContext",
    "PreviewForget",
    "PersonNotFoundError",
    "PersonNameCollisionError",
    "RecordFact",
    "RecordFactInput",
    "RecordInteraction",
    "RecordInteractionInput",
    "RecordNotFoundError",
    "RecordObservation",
    "RecordObservationInput",
    "RecordTrait",
    "RecordTraitInput",
    "RememberPerson",
    "RememberPersonInput",
    "RememberPersonResult",
    "ReindexPeople",
    "ReindexPeopleResult",
    "ResolutionCandidate",
    "ResolutionHints",
    "ResolutionResult",
    "ReviewImport",
    "ResolvePerson",
    "SearchPeople",
    "SetAffiliation",
    "SetAffiliationInput",
    "SetCommunicationPhilosophy",
    "SetCommunicationPhilosophyInput",
    "SetRelationship",
    "SetRelationshipInput",
    "SetReminder",
    "SetReminderInput",
    "SelfAlreadyExistsError",
    "ReminderNotActiveError",
]
