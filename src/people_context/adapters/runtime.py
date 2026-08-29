"""Shared runtime composition for CLI and MCP process entrypoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from people_context.adapters.filesystem.vault_writer import FileSystemVaultWriter
from people_context.adapters.filesystem.vcard_writer import CanonicalVCardWriter
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.importers.stable_source import VerifiedSnapshotExtractor
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
from people_context.adapters.sqlite.bootstrap_restore import SqliteBootstrapRestorer
from people_context.adapters.sqlite.bundle_reader import SqliteBundleReader
from people_context.adapters.sqlite.changelog import SqliteChangelog
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.curation_reader import SqliteCurationReader
from people_context.adapters.sqlite.db import open_db, open_encrypted_db
from people_context.adapters.sqlite.export_reader import SqliteExportReader
from people_context.adapters.sqlite.forget_store import SqliteForgetStore
from people_context.adapters.sqlite.graph_reader import SqliteGraphReader
from people_context.adapters.sqlite.hlc import SqliteHybridLogicalClock
from people_context.adapters.sqlite.import_staging import SqliteImportStagingStore
from people_context.adapters.sqlite.insights_reader import SqliteRecencyReader
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
from people_context.adapters.sqlite.source_store import SqliteImportSourceStore
from people_context.adapters.sqlite.stats_reader import SqliteStatsReader
from people_context.adapters.sqlite.timeline_reader import SqlitePersonTimelineReader
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.adapters.sqlite.vault_reader import SqliteVaultReader
from people_context.app.context import (
    GetCommunicationGuidance,
    GetPersonContext,
    ReportStoreStats,
    SetCommunicationPhilosophy,
)
from people_context.app.exports import (
    ComposePersonBrief,
    ExportData,
    ExportReminderCalendar,
    ExportSyncBundle,
    ExportVault,
    ExportVCard,
    ListPersonIndex,
)
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportContent,
    ListImportSources,
    PreflightImportBatch,
    ReviewImport,
    ShowImportSource,
    StageCandidates,
)
from people_context.app.insights import GetPersonTimeline, GetStaleRelationships, ListUpcomingDates
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
    ReportDoctorFindings,
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
from people_context.app.sync import RestoreSyncBundle, WatchChangelog
from people_context.config import resolve_db_key, resolve_db_path
from people_context.ports.clock import Clock, SystemClock
from people_context.ports.sleep import Sleeper, SystemSleeper

WarningCallback = Callable[[str], None]


@dataclass(frozen=True)
class RuntimeUseCases:
    """Application use cases shared by process adapters."""

    resolve_person: ResolvePerson
    get_person_context: GetPersonContext
    get_relationship_graph: GetRelationshipGraph
    find_connection: FindConnection
    get_stale_relationships: GetStaleRelationships
    get_person_timeline: GetPersonTimeline
    report_doctor_findings: ReportDoctorFindings
    report_store_stats: ReportStoreStats
    list_upcoming_dates: ListUpcomingDates
    list_person_index: ListPersonIndex
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
    compose_person_brief: ComposePersonBrief
    export_data: ExportData
    export_sync_bundle: ExportSyncBundle
    export_reminder_calendar: ExportReminderCalendar
    export_vcard: ExportVCard
    restore_sync_bundle: RestoreSyncBundle
    watch_changelog: WatchChangelog
    export_vault: ExportVault
    import_content: ImportContent
    review_import: ReviewImport
    preflight_import_batch: PreflightImportBatch
    commit_import: CommitImport
    stage_candidates: StageCandidates
    list_import_sources: ListImportSources
    show_import_source: ShowImportSource
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
    recency_reader: SqliteRecencyReader
    timeline_reader: SqlitePersonTimelineReader
    curation_reader: SqliteCurationReader
    stats_reader: SqliteStatsReader
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
    bundle_reader: SqliteBundleReader
    bootstrap_restorer: SqliteBootstrapRestorer
    vault_reader: SqliteVaultReader
    import_staging: SqliteImportStagingStore
    import_sources: SqliteImportSourceStore
    trait_evidence: SqliteTraitEvidenceStore
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
    sleeper: Sleeper | None = None,
    encrypted: bool = False,
) -> ApplicationRuntime:
    """Build all concrete adapters and application use cases for one process.

    `encrypted` selects the opt-in SQLCipher connection, whose key is read only
    from the environment. It refuses rather than falling back to plaintext.
    """
    warn = warning or (lambda _message: None)
    path = resolve_db_path(db_path)
    conn = open_encrypted_db(path, resolve_db_key()) if encrypted else open_db(path)
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
            "Run `uv run pctx reindex --semantic`."
        )
        semantic_updater = None
    if semantic_updater is not None:
        repo = IndexingPeopleRepository(repo, semantic_updater, warn)
        records = IndexingRecordStore(records, semantic_updater, warn)
        merge_store = IndexingMergeStore(merge_store, semantic_updater, warn)
        forget_store = IndexingForgetStore(forget_store, semantic_updater, warn)

    context_reader = SqliteContextReader(conn)
    graph_reader = SqliteGraphReader(conn, runtime_clock)
    recency_reader = SqliteRecencyReader(conn)
    timeline_reader = SqlitePersonTimelineReader(conn)
    curation_reader = SqliteCurationReader(conn)
    stats_reader = SqliteStatsReader(conn, path)
    relationship_store = SqliteRelationshipStore(conn)
    relationship_vocabulary = SqliteRelationshipVocabularyStore(conn)
    organizations = SqliteOrganizationStore(conn)
    preferences = SqlitePreferencesStore(conn, runtime_clock)
    audit = SqliteAuditLog(conn)
    changelog = SqliteChangelog(conn)
    export_reader = SqliteExportReader(conn)
    bundle_reader = SqliteBundleReader(conn)
    bootstrap_restorer = SqliteBootstrapRestorer(conn, repo, SqliteHybridLogicalClock(conn))
    vault_reader = SqliteVaultReader(conn, runtime_clock)
    import_staging = SqliteImportStagingStore(conn)
    import_sources = SqliteImportSourceStore(conn)
    trait_evidence = SqliteTraitEvidenceStore(conn)
    semantic_documents = SqliteSemanticDocumentReader(conn)

    remember_person = RememberPerson(repo, repo, audit, runtime_clock)
    record_interaction = RecordInteraction(repo, records, audit, runtime_clock)
    set_affiliation = SetAffiliation(repo, organizations, records, audit, runtime_clock)
    record_fact = RecordFact(repo, records, audit, runtime_clock)
    record_observation = RecordObservation(repo, records, audit, runtime_clock)
    record_trait = RecordTrait(repo, records, audit, runtime_clock, trait_evidence)
    set_relationship = SetRelationship(repo, relationship_store, audit, runtime_clock, relationship_vocabulary)
    candidate_stager = CandidateStager(repo, import_staging, runtime_clock, import_sources, audit)
    list_reminders = ListReminders(records)
    get_person_context = GetPersonContext(repo, context_reader, runtime_clock)
    get_communication_guidance = GetCommunicationGuidance(repo, context_reader, preferences, runtime_clock)

    use_cases = RuntimeUseCases(
        resolve_person=ResolvePerson(repo, context_reader, runtime_clock),
        get_person_context=get_person_context,
        get_relationship_graph=GetRelationshipGraph(repo, graph_reader, relationship_vocabulary),
        find_connection=FindConnection(repo, graph_reader, relationship_vocabulary),
        get_stale_relationships=GetStaleRelationships(recency_reader, runtime_clock),
        get_person_timeline=GetPersonTimeline(repo, timeline_reader),
        report_doctor_findings=ReportDoctorFindings(curation_reader, runtime_clock),
        report_store_stats=ReportStoreStats(stats_reader, runtime_clock),
        list_upcoming_dates=ListUpcomingDates(context_reader, list_reminders, repo, runtime_clock),
        list_person_index=ListPersonIndex(repo, runtime_clock),
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
        set_relationship=set_relationship,
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
        record_observation=record_observation,
        record_trait=record_trait,
        record_interaction=record_interaction,
        correct_record=CorrectRecord(records, records, audit, runtime_clock, people=repo),
        set_reminder=SetReminder(repo, records, audit, runtime_clock),
        complete_reminder=CompleteReminder(records, records, audit, runtime_clock, people=repo),
        set_communication_philosophy=SetCommunicationPhilosophy(preferences, audit, runtime_clock),
        get_communication_guidance=get_communication_guidance,
        list_reminders=list_reminders,
        merge_people=MergePeople(repo, merge_store, runtime_clock, audit),
        preview_forget=PreviewForget(repo, forget_store),
        forget=Forget(repo, forget_store, runtime_clock, audit),
        compose_person_brief=ComposePersonBrief(
            get_person_context,
            get_communication_guidance,
            list_reminders,
            runtime_clock,
        ),
        export_data=ExportData(export_reader, runtime_clock),
        export_sync_bundle=ExportSyncBundle(bundle_reader, runtime_clock),
        export_reminder_calendar=ExportReminderCalendar(list_reminders),
        export_vcard=ExportVCard(export_reader, CanonicalVCardWriter(), runtime_clock),
        restore_sync_bundle=RestoreSyncBundle(bootstrap_restorer),
        watch_changelog=WatchChangelog(changelog, sleeper or SystemSleeper()),
        export_vault=ExportVault(vault_reader, FileSystemVaultWriter()),
        import_content=ImportContent(
            repo,
            ImportExtractorRouter(),
            import_staging,
            runtime_clock,
            candidate_stager,
            VerifiedSnapshotExtractor(),
        ),
        review_import=ReviewImport(import_staging),
        preflight_import_batch=PreflightImportBatch(import_staging),
        commit_import=CommitImport(
            repo,
            import_staging,
            remember_person,
            record_interaction,
            set_affiliation,
            record_fact,
            record_observation,
            record_trait,
            set_relationship,
            import_sources,
            audit,
            runtime_clock,
            trait_evidence,
        ),
        stage_candidates=StageCandidates(candidate_stager),
        list_import_sources=ListImportSources(import_sources),
        show_import_source=ShowImportSource(import_sources),
        reindex_people=ReindexPeople(repo),
    )
    return ApplicationRuntime(
        path=path,
        conn=conn,
        clock=runtime_clock,
        repo=repo,
        context_reader=context_reader,
        graph_reader=graph_reader,
        recency_reader=recency_reader,
        timeline_reader=timeline_reader,
        curation_reader=curation_reader,
        stats_reader=stats_reader,
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
        bundle_reader=bundle_reader,
        bootstrap_restorer=bootstrap_restorer,
        vault_reader=vault_reader,
        import_staging=import_staging,
        import_sources=import_sources,
        trait_evidence=trait_evidence,
        semantic_documents=semantic_documents,
        use_cases=use_cases,
    )
