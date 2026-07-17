"""FastMCP server wiring with stdio and loopback Streamable HTTP transports.

This adapter is the only place the MCP SDK is imported. It resolves the database
path, opens the SQLite store, constructs the repository/audit/clock and the app
use cases, and injects them into the tool layer. All logging goes to STDERR — the
stdio transport uses STDOUT for the protocol itself, so anything printed there
would corrupt the stream.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from people_context.adapters.email_import import EmailImportExtractor
from people_context.adapters.mcp.tools import register_all
from people_context.adapters.semantic_indexing import (
    IndexingLifecycleStore,
    IndexingPeopleRepository,
    IndexingRecordStore,
    create_local_semantic_updater,
)
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteExportReader,
    SqliteImportStagingStore,
    SqliteLifecycleStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePreferencesStore,
    SqliteRecordStore,
    open_db,
)
from people_context.app import (
    AddAlias,
    CommitImport,
    CompleteReminder,
    CorrectRecord,
    ExportData,
    Forget,
    GetCommunicationGuidance,
    GetPersonContext,
    ImportContent,
    ListReminders,
    MergePeople,
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    RememberPerson,
    ResolvePerson,
    ReviewImport,
    SearchPeople,
    SetAffiliation,
    SetCommunicationPhilosophy,
    SetRelationship,
    SetReminder,
)
from people_context.config import resolve_db_path
from people_context.ports.clock import SystemClock

SERVER_NAME = "people-context"

SERVER_INSTRUCTIONS = (
    "people-context stores durable, local-first knowledge about the people in the user's life: "
    "their names and aliases, how they relate to the user, their organisations and roles, and "
    "relevant past interactions. "
    "When the user mentions someone by name, nickname, or partial reference, call `resolve_person` "
    "first to find who they mean — prefer resolving before asking the user who someone is. "
    "After resolving an identity, use `get_person_context` for a bounded, sensitivity-aware context bundle. "
    "Use `search_people` for broader browsing and `remember_person` to record a new or updated person. "
    "Read-only tools are safe to call freely; write and destructive tools are annotated so the client "
    "can gate them behind its normal approval flow."
)

_LOG_LOGGER_NAME = "people_context"


@dataclass(frozen=True)
class ToolDeps:
    """Use-case dependencies injected into the tool layer (no globals/singletons)."""

    resolve_person: ResolvePerson
    get_person_context: GetPersonContext
    search_people: SearchPeople
    remember_person: RememberPerson
    add_alias: AddAlias
    set_relationship: SetRelationship
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
    forget: Forget
    export_data: ExportData
    import_content: ImportContent
    review_import: ReviewImport
    commit_import: CommitImport


def _configure_logging() -> logging.Logger:
    """Configure a single STDERR handler for the package logger and return it.

    Rebinds the handler to the current ``sys.stderr`` on every call so the log
    line is never emitted to STDOUT (which carries the stdio protocol).
    """
    logger = logging.getLogger(_LOG_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def build_server(db_path: str | Path | None = None) -> FastMCP:
    """Build a fully wired FastMCP server backed by the resolved SQLite database.

    Resolves ``db_path`` via :func:`resolve_db_path`, logs the chosen path to
    STDERR, opens the database, constructs the repository/audit/clock and the
    application use cases, and registers every tool. Does not start any transport.
    """
    logger = _configure_logging()
    path = resolve_db_path(db_path)
    logger.info("people-context MCP server using database at %s", path)

    conn = open_db(path)
    repository = SqlitePeopleRepository(conn)
    context_reader = SqliteContextReader(conn)
    record_store = SqliteRecordStore(conn)
    organization_store = SqliteOrganizationStore(conn)
    preferences_store = SqlitePreferencesStore(conn)
    audit = SqliteAuditLog(conn)
    lifecycle_store = SqliteLifecycleStore(conn)
    export_reader = SqliteExportReader(conn)
    import_staging = SqliteImportStagingStore(conn)
    clock = SystemClock()
    try:
        semantic_updater = create_local_semantic_updater(conn)
    except Exception as exc:  # noqa: BLE001 - optional derived index cannot block the server
        logger.warning(
            "Semantic index maintenance is unavailable: %s. Run `uv run people-context reindex --semantic`.",
            exc,
        )
        semantic_updater = None
    if semantic_updater is not None:
        warn = logger.warning
        repository = IndexingPeopleRepository(repository, semantic_updater, warn)
        record_store = IndexingRecordStore(record_store, semantic_updater, warn)
        lifecycle_store = IndexingLifecycleStore(lifecycle_store, semantic_updater, warn)
    remember_person = RememberPerson(repository, repository, audit, clock)
    record_interaction = RecordInteraction(repository, record_store, audit, clock)

    deps = ToolDeps(
        resolve_person=ResolvePerson(repository, context_reader, clock),
        get_person_context=GetPersonContext(repository, context_reader, clock),
        search_people=SearchPeople(repository),
        remember_person=remember_person,
        add_alias=AddAlias(repository, repository, audit, clock),
        set_relationship=SetRelationship(repository, record_store, audit, clock),
        set_affiliation=SetAffiliation(repository, organization_store, record_store, audit, clock),
        record_fact=RecordFact(repository, record_store, audit, clock),
        record_observation=RecordObservation(repository, record_store, audit, clock),
        record_trait=RecordTrait(repository, record_store, audit, clock),
        record_interaction=record_interaction,
        correct_record=CorrectRecord(record_store, record_store, audit, clock, people=repository),
        set_reminder=SetReminder(repository, record_store, audit, clock),
        complete_reminder=CompleteReminder(record_store, record_store, audit, clock, people=repository),
        set_communication_philosophy=SetCommunicationPhilosophy(preferences_store, audit, clock),
        get_communication_guidance=GetCommunicationGuidance(
            repository, context_reader, preferences_store, clock
        ),
        list_reminders=ListReminders(record_store),
        merge_people=MergePeople(repository, lifecycle_store, clock),
        forget=Forget(repository, lifecycle_store, clock),
        export_data=ExportData(export_reader, clock),
        import_content=ImportContent(repository, EmailImportExtractor(), import_staging, clock),
        review_import=ReviewImport(import_staging),
        commit_import=CommitImport(repository, import_staging, remember_person, record_interaction),
    )

    mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_all(mcp, deps)
    return mcp


def _build_parser() -> argparse.ArgumentParser:
    """Build the server entrypoint parser without constructing application state."""
    parser = argparse.ArgumentParser(
        prog="people-context-mcp",
        description="Local-first MCP server with contextual knowledge about the people in your life.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Path to the SQLite database file (overrides env/config/auto-detect).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP on loopback instead of stdio.",
    )
    parser.add_argument(
        "--host",
        choices=("127.0.0.1",),
        default="127.0.0.1",
        help="HTTP bind host; only 127.0.0.1 is accepted (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP bind port (default: 8765).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Select stdio by default or explicitly configured loopback Streamable HTTP."""
    args = _build_parser().parse_args(argv)
    server = build_server(args.db)
    if not args.http:
        server.run()
        return

    server.settings.host = args.host
    server.settings.port = args.port
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
