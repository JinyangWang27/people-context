"""Shared runtime composition for CLI and MCP process entrypoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from people_context.adapters.filesystem.vault_writer import FileSystemVaultWriter
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.model2vec_embeddings import (
    MODEL_DIMENSION,
    MODEL_ID,
    create_local_embedding_provider,
)
from people_context.adapters.semantic_indexing import (
    IndexingForgetStore,
    IndexingMergeStore,
    IndexingPeopleRepository,
    IndexingRecordStore,
    create_local_semantic_updater,
)
from people_context.adapters.sqlite.audit_log import SqliteAuditLog
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.forget_store import SqliteForgetStore
from people_context.adapters.sqlite.graph_reader import SqliteGraphReader
from people_context.adapters.sqlite.import_staging import SqliteImportStagingStore
from people_context.adapters.sqlite.merge_store import SqliteMergeStore
from people_context.adapters.sqlite.organization_store import SqliteOrganizationStore
from people_context.adapters.sqlite.preferences_store import SqlitePreferencesStore
from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.relationship_store import SqliteRelationshipStore
from people_context.adapters.sqlite.relationship_vocabulary import SqliteRelationshipVocabularyStore
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.adapters.sqlite.semantic import (
    SqliteSemanticDocumentReader,
    SqliteSemanticEntityReader,
    SqliteSemanticMetadataReader,
    open_sqlite_vector_index,
)
from people_context.adapters.sqlite.vault_reader import SqliteVaultReader
from people_context.app.context import (
    GetCommunicationGuidance,
    GetPersonContext,
    SetCommunicationPhilosophy,
)
from people_context.app.exports import ExportData, ExportVault
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportContent,
    ReviewImport,
    StageCandidates,
)
from people_context.app.people import (
    AddAlias,
    EditPerson,
    Forget,
    MergePeople,
    PreviewForget,
    RememberPerson,
    ResolvePerson,
    SearchPeople,
)
from people_context.app.records import (
    CompleteReminder,
    CorrectRecord,
    ListReminders,
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
    SetReminder,
)
from people_context.app.relationships import (
    AddRelationshipType,
    FindConnection,
    GetRelationshipGraph,
    NormalizeRelationships,
    SetRelationship,
)
from people_context.app.semantic import ReindexPeople, SemanticSearch
from people_context.config import resolve_db_path
from people_context.ports.clock import Clock, SystemClock

WarningCallback = Callable[[str], None]


@dataclass(frozen=True)
class RuntimeUseCases:
    """Application use cases shared by process adapters."""

    resolve_person: ResolvePerson
    get_person_context: GetPersonContext
    get_relationship_graph: GetRelationshipGraph
    find_connection: FindConnection
    search_people: SearchPeople
    semantic_search: SemanticSearch
    remember_person: RememberPerson
    edit_person: EditPerson
    add_alias: AddAlias
    set_relationship: SetRelationship
    add_relationship_type: AddRelationshipType
    normalize_relationships: NormalizeRelationships
    set_affiliation: SetAffiliation
    record_fact: RecordFact
    record_observation: RecordObservation
    record_trait: RecordTrait
    record_interaction: RecordInteraction
    correct_record: CorrectRecord
    set_reminder: SetReminder
    complete_reminder: CompleteReminder
    set_communication_philosophy: SetCommunicationPhilosophy
    get_communication_guidance: GetCommunicationGuidance
    list_reminders: ListReminders
    merge_people: MergePeople
    preview_forget: PreviewForget
    forget: Forget
    export_data: ExportData
    export_vault: ExportVault
    import_content: ImportContent
    review_import: ReviewImport
    commit_import: CommitImport
    stage_candidates: StageCandidates
    reindex_people: ReindexPeople


@dataclass(frozen=True)
class ApplicationRuntime:
    """Concrete adapters and use cases owned by one process invocation."""

    path: Path
    conn: sqlite3.Connection
    clock: Clock
    repo: SqlitePeopleRepository | IndexingPeopleRepository
    context_reader: SqliteContextReader
    graph_reader: SqliteGraphReader
    records: SqliteRecordStore | IndexingRecordStore
    relationship_store: SqliteRelationshipStore
    relationship_vocabulary: SqliteRelationshipVocabularyStore
    organizations: SqliteOrganizationStore
    preferences: SqlitePreferencesStore
    audit: SqliteAuditLog
    changelog: SqliteChangelog
    merge_store: SqliteMergeStore | IndexingMergeStore
    forget_store: SqliteForgetStore | IndexingForgetStore
    export_reader: SqliteExportReader
    vault_reader: SqliteVaultReader
    import_staging: SqliteImportStagingStore
    semantic_documents: SqliteSemanticDocumentReader
    use_cases: RuntimeUseCases

    def close(self) -> None:
        """Close the runtime's owned SQLite connection."""
        self.conn.close()


def build_runtime(
    db_path: str | Path | None = None,
    *,
    warning: WarningCallback | None = None,
    clock: Clock | None = None,
) -> ApplicationRuntime:
    """Build all concrete adapters and application use cases for one process."""
    warn = warning or (lambda _message: None)
    path = resolve_db_path(db_path)
    conn = open_db(path)
    runtime_clock = clock or SystemClock()
    repo: SqlitePeopleRepository | IndexingPeopleRepository = SqlitePeopleRepository(conn)
    records: SqliteRecordStore | IndexingRecordStore = SqliteRecordStore(conn)
    merge_store: SqliteMergeStore | IndexingMergeStore = SqliteMergeStore(conn)
    forget_store: SqliteForgetStore | IndexingForgetStore = SqliteForgetStore(conn)

    try:
        semantic_updater = create_local_semantic_updater(conn)
    except Exception as exc:  # noqa: BLE001 - optional derived index cannot block primary operations
        warn(
            f"Semantic index maintenance is unavailable: {exc}. "
            "Run `uv run people-context reindex --semantic`."
        )
        semantic_updater = None
    if semantic_updater is not None:
        repo = IndexingPeopleRepository(repo, semantic_updater, warn)
        records = IndexingRecordStore(records, semantic_updater, warn)
        merge_store = IndexingMergeStore(merge_store, semantic_updater, warn)
        forget_store = IndexingForgetStore(forget_store, semantic_updater, warn)

    context_reader = SqliteContextReader(conn)
    graph_reader = SqliteGraphReader(conn, runtime_clock)
    relationship_store = SqliteRelationshipStore(conn)
    relationship_vocabulary = SqliteRelationshipVocabularyStore(conn)
    organizations = SqliteOrganizationStore(conn)
    preferences = SqlitePreferencesStore(conn, runtime_clock)
    audit = SqliteAuditLog(conn)
    changelog = SqliteChangelog(conn)
    export_reader = SqliteExportReader(conn)
    vault_reader = SqliteVaultReader(conn, runtime_clock)
    import_staging = SqliteImportStagingStore(conn)
    semantic_documents = SqliteSemanticDocumentReader(conn)

    remember_person = RememberPerson(repo, repo, audit, runtime_clock)
    record_interaction = RecordInteraction(repo, records, audit, runtime_clock)
    set_affiliation = SetAffiliation(repo, organizations, records, audit, runtime_clock)
    record_fact = RecordFact(repo, records, audit, runtime_clock)
    candidate_stager = CandidateStager(repo, import_staging, runtime_clock)

    use_cases = RuntimeUseCases(
        resolve_person=ResolvePerson(repo, context_reader, runtime_clock),
        get_person_context=GetPersonContext(repo, context_reader, runtime_clock),
        get_relationship_graph=GetRelationshipGraph(repo, graph_reader, relationship_vocabulary),
        find_connection=FindConnection(repo, graph_reader, relationship_vocabulary),
        search_people=SearchPeople(repo),
        semantic_search=SemanticSearch(
            SqliteSemanticMetadataReader(conn),
            SqliteSemanticEntityReader(conn),
            create_local_embedding_provider,
            lambda: open_sqlite_vector_index(conn),
            MODEL_ID,
            MODEL_DIMENSION,
        ),
        remember_person=remember_person,
        edit_person=EditPerson(repo, repo, audit, runtime_clock),
        add_alias=AddAlias(repo, repo, audit, runtime_clock),
        set_relationship=SetRelationship(
            repo,
            relationship_store,
            audit,
            runtime_clock,
            relationship_vocabulary,
        ),
        add_relationship_type=AddRelationshipType(
            relationship_vocabulary,
            relationship_vocabulary,
            audit,
            runtime_clock,
        ),
        normalize_relationships=NormalizeRelationships(
            relationship_store,
            relationship_vocabulary,
            audit,
            runtime_clock,
        ),
        set_affiliation=set_affiliation,
        record_fact=record_fact,
        record_observation=RecordObservation(repo, records, audit, runtime_clock),
        record_trait=RecordTrait(repo, records, audit, runtime_clock),
        record_interaction=record_interaction,
        correct_record=CorrectRecord(records, records, audit, runtime_clock, people=repo),
        set_reminder=SetReminder(repo, records, audit, runtime_clock),
        complete_reminder=CompleteReminder(records, records, audit, runtime_clock, people=repo),
        set_communication_philosophy=SetCommunicationPhilosophy(preferences, audit, runtime_clock),
        get_communication_guidance=GetCommunicationGuidance(repo, context_reader, preferences, runtime_clock),
        list_reminders=ListReminders(records),
        merge_people=MergePeople(repo, merge_store, runtime_clock, audit),
        preview_forget=PreviewForget(repo, forget_store),
        forget=Forget(repo, forget_store, runtime_clock, audit),
        export_data=ExportData(export_reader, runtime_clock),
        export_vault=ExportVault(vault_reader, FileSystemVaultWriter()),
        import_content=ImportContent(
            repo,
            ImportExtractorRouter(),
            import_staging,
            runtime_clock,
            candidate_stager,
        ),
        review_import=ReviewImport(import_staging),
        commit_import=CommitImport(
            repo,
            import_staging,
            remember_person,
            record_interaction,
            set_affiliation,
            record_fact,
        ),
        stage_candidates=StageCandidates(candidate_stager),
        reindex_people=ReindexPeople(repo),
    )
    return ApplicationRuntime(
        path=path,
        conn=conn,
        clock=runtime_clock,
        repo=repo,
        context_reader=context_reader,
        graph_reader=graph_reader,
        records=records,
        relationship_store=relationship_store,
        relationship_vocabulary=relationship_vocabulary,
        organizations=organizations,
        preferences=preferences,
        audit=audit,
        changelog=changelog,
        merge_store=merge_store,
        forget_store=forget_store,
        export_reader=export_reader,
        vault_reader=vault_reader,
        import_staging=import_staging,
        semantic_documents=semantic_documents,
        use_cases=use_cases,
    )
