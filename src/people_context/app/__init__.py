"""Application layer: use cases orchestrating domain entities over the ports."""

from __future__ import annotations

from people_context.app.add_alias import AddAlias, AddAliasInput
from people_context.app.add_relationship_type import (
    AddRelationshipType,
    AddRelationshipTypeInput,
    RelationshipTypeAlreadyExistsError,
)
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
    AffiliationCandidateInput,
    CandidateAlias,
    CandidateStager,
    CommitImport,
    CommitImportResult,
    FactCandidateInput,
    ImportBatchResult,
    ImportContent,
    ImportPipelineError,
    ImportReviewResult,
    ImportReviewRow,
    InteractionCandidateInput,
    PersonCandidateInput,
    ReviewImport,
    StageCandidates,
)
from people_context.app.list_reminders import ListReminders, ListRemindersInput
from people_context.app.merge_people import MergeMovedCounts, MergePeople, MergePeopleError, MergePeopleResult
from people_context.app.normalize_relationships import (
    NormalizeRelationships,
    NormalizeRelationshipsResult,
    RelationshipNormalizationChange,
)
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
from people_context.app.reindex_semantic import ReindexSemantic, ReindexSemanticResult
from people_context.app.relationship_graph import (
    ConnectionEdgeResult,
    ConnectionHop,
    ConnectionResult,
    FindConnection,
    GetRelationshipGraph,
    GraphEdgeResult,
    GraphPersonNotFound,
    GraphPersonResult,
    GraphTraversalError,
    RelationshipGraphResult,
)
from people_context.app.resolve_person import ResolutionCandidate, ResolutionHints, ResolutionResult, ResolvePerson
from people_context.app.search_people import SearchPeople
from people_context.app.semantic_search import (
    SemanticSearch,
    SemanticSearchHit,
    SemanticSearchModelMismatch,
    SemanticSearchNotAvailable,
    SemanticSearchOk,
    SemanticSearchValidationError,
)
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
    "AddAlias",
    "AddAliasInput",
    "AddRelationshipType",
    "AddRelationshipTypeInput",
    "AffiliationCandidateInput",
    "AliasInput",
    "AmbiguousPersonError",
    "CandidateAlias",
    "CandidateStager",
    "CommitImport",
    "CommitImportResult",
    "CommunicationGuidanceResult",
    "ConnectionEdgeResult",
    "ConnectionHop",
    "ConnectionResult",
    "CompleteReminder",
    "CompleteReminderInput",
    "CorrectRecord",
    "CorrectRecordInput",
    "EditPerson",
    "EditPersonInput",
    "ExportData",
    "ExportDocument",
    "FactCandidateInput",
    "Forget",
    "ForgetError",
    "ForgetPreview",
    "FindConnection",
    "ForgetResult",
    "GetCommunicationGuidance",
    "GetRelationshipGraph",
    "GetPersonContext",
    "GraphEdgeResult",
    "GraphPersonNotFound",
    "GraphPersonResult",
    "GraphTraversalError",
    "ImportBatchResult",
    "ImportContent",
    "ImportPipelineError",
    "ImportReviewResult",
    "ImportReviewRow",
    "InteractionCandidateInput",
    "InvalidCorrectionError",
    "InvalidReminderError",
    "ListReminders",
    "ListRemindersInput",
    "MergeMovedCounts",
    "MergePeople",
    "MergePeopleError",
    "MergePeopleResult",
    "NormalizeRelationships",
    "NormalizeRelationshipsResult",
    "OrganizationNotFoundError",
    "PersonAffiliationContext",
    "PersonCandidateInput",
    "PersonContextResult",
    "PersonIdentity",
    "PersonNameCollisionError",
    "PersonNotFoundError",
    "PersonRelationshipContext",
    "PreviewForget",
    "RecordFact",
    "RecordFactInput",
    "RecordInteraction",
    "RecordInteractionInput",
    "RecordNotFoundError",
    "RecordObservation",
    "RecordObservationInput",
    "RecordTrait",
    "RecordTraitInput",
    "ReindexPeople",
    "ReindexPeopleResult",
    "ReindexSemantic",
    "ReindexSemanticResult",
    "RelationshipGraphResult",
    "RelationshipNormalizationChange",
    "RelationshipTypeAlreadyExistsError",
    "RememberPerson",
    "RememberPersonInput",
    "RememberPersonResult",
    "ReminderNotActiveError",
    "ResolutionCandidate",
    "ResolutionHints",
    "ResolutionResult",
    "ResolvePerson",
    "ReviewImport",
    "SearchPeople",
    "SelfAlreadyExistsError",
    "SemanticSearch",
    "SemanticSearchHit",
    "SemanticSearchModelMismatch",
    "SemanticSearchNotAvailable",
    "SemanticSearchOk",
    "SemanticSearchValidationError",
    "SetAffiliation",
    "SetAffiliationInput",
    "SetCommunicationPhilosophy",
    "SetCommunicationPhilosophyInput",
    "SetRelationship",
    "SetRelationshipInput",
    "SetReminder",
    "SetReminderInput",
    "StageCandidates",
]
