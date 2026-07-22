"""Command-line inspection and curation over shared app-layer use cases."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from people_context.adapters.email_import import ImportExtractionError
from people_context.adapters.filesystem import FileSystemVaultWriter
from people_context.adapters.import_router import ImportExtractorRouter
from people_context.adapters.model2vec_embeddings import (
    MODEL_DOWNLOAD_SIZE,
    MODEL_ID,
    MODEL_URL,
    download_embedding_provider,
    semantic_cache_dir,
)
from people_context.adapters.semantic_indexing import (
    IndexingLifecycleStore,
    IndexingPeopleRepository,
    create_local_semantic_updater,
)
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteContextReader,
    SqliteExportReader,
    SqliteImportStagingStore,
    SqliteLifecycleStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePreferencesStore,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    SqliteSemanticDocumentReader,
    SqliteVaultReader,
    create_sqlite_vector_index,
    open_db,
)
from people_context.app import (
    AddAlias,
    AddAliasInput,
    AddRelationshipType,
    AddRelationshipTypeInput,
    AliasInput,
    AmbiguousPersonError,
    CommitImport,
    EditPerson,
    EditPersonInput,
    ExportData,
    ExportVault,
    Forget,
    GetPersonContext,
    ImportContent,
    ImportPipelineError,
    ImportReviewRow,
    NormalizeRelationships,
    PersonContextResult,
    PersonNameCollisionError,
    PreviewForget,
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    ReindexPeople,
    ReindexSemantic,
    RelationshipTypeAlreadyExistsError,
    RememberPerson,
    RememberPersonInput,
    ResolvePerson,
    ReviewImport,
    SearchPeople,
    SelfAlreadyExistsError,
    SetAffiliation,
    SetAffiliationInput,
    SetCommunicationPhilosophy,
    SetCommunicationPhilosophyInput,
    SetRelationship,
    SetRelationshipInput,
)
from people_context.config import describe_resolution, resolve_db_path
from people_context.demo_seed import (
    DEMO_AFFILIATIONS,
    DEMO_FACTS,
    DEMO_INTERACTIONS,
    DEMO_PEOPLE,
    DEMO_RELATIONSHIPS,
)
from people_context.domain.person import AliasKind, Person
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock, SystemClock
from people_context.ports.vault import VaultSafetyError

_SUMMARY_WIDTH = 40
_DEMO_FILENAME = "demo.db"


@dataclass
class CliContext:
    """Per-invocation composition of the adapters a DB-backed command needs."""

    conn: sqlite3.Connection
    repo: SqlitePeopleRepository | IndexingPeopleRepository
    context_reader: SqliteContextReader
    clock: Clock
    export_reader: SqliteExportReader
    audit: SqliteAuditLog
    lifecycle: SqliteLifecycleStore | IndexingLifecycleStore
    preferences: SqlitePreferencesStore
    changelog: SqliteChangelog | None = None
    vault_reader: SqliteVaultReader | None = None
    relationship_store: SqliteRelationshipStore | None = None
    relationship_vocabulary: SqliteRelationshipVocabularyStore | None = None


def _open_context(db: str | None) -> CliContext:
    return _open_context_path(resolve_db_path(db))


def _open_context_path(db_path: str | Path) -> CliContext:
    """Compose a CLI context for an already-resolved database path."""
    conn = open_db(db_path)
    clock = SystemClock()
    repo: SqlitePeopleRepository | IndexingPeopleRepository = SqlitePeopleRepository(conn)
    lifecycle: SqliteLifecycleStore | IndexingLifecycleStore = SqliteLifecycleStore(conn)
    try:
        semantic_updater = create_local_semantic_updater(conn)
    except Exception as exc:  # noqa: BLE001 - optional derived index cannot block primary CLI operations
        print(
            f"Warning: semantic index maintenance is unavailable: {exc}. "
            "Run `uv run people-context reindex --semantic`.",
            file=sys.stderr,
        )
        semantic_updater = None
    if semantic_updater is not None:

        def warn(message: str) -> None:
            print(f"Warning: {message}", file=sys.stderr)

        repo = IndexingPeopleRepository(repo, semantic_updater, warn)
        lifecycle = IndexingLifecycleStore(lifecycle, semantic_updater, warn)
    return CliContext(
        conn=conn,
        repo=repo,
        context_reader=SqliteContextReader(conn),
        clock=clock,
        export_reader=SqliteExportReader(conn),
        vault_reader=SqliteVaultReader(conn, clock),
        audit=SqliteAuditLog(conn),
        changelog=SqliteChangelog(conn),
        lifecycle=lifecycle,
        preferences=SqlitePreferencesStore(conn, clock),
        relationship_store=SqliteRelationshipStore(conn),
        relationship_vocabulary=SqliteRelationshipVocabularyStore(conn),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="people-context", description="Inspect and search your people data.")
    parser.add_argument("--db", default=None, help="Explicit database path, overriding other resolution sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_path = subparsers.add_parser("db-path", help="Print the resolved database path.")
    db_path.add_argument("-v", "--verbose", action="store_true", help="Show the full resolution trace.")

    list_cmd = subparsers.add_parser("list", help="List known people.")
    list_cmd.add_argument("--all", action="store_true", help="Include soft-deleted people.")
    list_cmd.add_argument("--limit", type=int, default=None, help="Maximum number of people to list.")

    search = subparsers.add_parser("search", help="Ranked search results for a name query.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10, help="Maximum number of results.")

    show = subparsers.add_parser("show", help="Show a person's full record.")
    show.add_argument("person", help="A person id, or a name to resolve.")

    export = subparsers.add_parser("export", help="JSON dump of all people.")
    export.add_argument("--output", default=None, help="Write to this file instead of stdout.")

    export_vault = subparsers.add_parser("export-vault", help="Export an Obsidian relationship vault.")
    export_vault.add_argument("--output", required=True, help="Empty or marker-owned output directory.")
    export_vault.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include sensitive and restricted facts in files outside server disclosure controls.",
    )

    edit = subparsers.add_parser("edit", help="Edit a person's canonical name or summary.")
    edit.add_argument("person", help="An active person id, or a name to resolve.")
    edit.add_argument("--name", default=None, help="New canonical name.")
    edit.add_argument("--summary", default=None, help="New summary.")

    add_alias = subparsers.add_parser("add-alias", help="Add an alias to a person.")
    add_alias.add_argument("person", help="An active person id, or a name to resolve.")
    add_alias.add_argument("value")
    add_alias.add_argument("--kind", choices=[kind.value for kind in AliasKind], default=AliasKind.OTHER.value)
    add_alias.add_argument("--lang", default=None)
    add_alias.add_argument("--script", default=None)

    set_cmd = subparsers.add_parser("set", help="Set a supported user preference.")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")

    delete = subparsers.add_parser("delete", help="Permanently forget a person and their linked data.")
    delete.add_argument("person", help="An active person id, or a name to resolve.")
    delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    relationship_types = subparsers.add_parser(
        "relationship-types",
        help="List relationship vocabulary or add a custom type.",
    )
    relationship_type_subcommands = relationship_types.add_subparsers(dest="relationship_types_command")
    relationship_type_add = relationship_type_subcommands.add_parser("add", help="Add custom vocabulary.")
    relationship_type_add.add_argument("type")
    relationship_type_add.add_argument("--category", required=True)
    direction = relationship_type_add.add_mutually_exclusive_group()
    direction.add_argument("--inverse", default=None)
    direction.add_argument("--symmetric", action="store_true")
    relationship_type_add.add_argument(
        "--synonym",
        action="append",
        default=[],
        help="Additional synonym; repeat for multiple values.",
    )

    normalize_relationships = subparsers.add_parser(
        "normalize-relationships",
        help="Preview or apply canonical rewrites to existing relationships.",
    )
    normalize_relationships.add_argument("--apply", action="store_true", help="Execute the reported rewrites.")

    sync_log = subparsers.add_parser("sync-log", help="Inspect the local replayable changelog.")
    sync_log.add_argument("--limit", type=int, default=50, help="Maximum number of recent entries.")
    sync_log.add_argument("--entity", default=None, help="Filter by exact entity id.")
    sync_log.add_argument(
        "--payloads",
        action="store_true",
        help="Include full replay payloads; hidden by default because they may contain sensitive data.",
    )

    reindex = subparsers.add_parser("reindex", help="Rebuild active-person full-text search rows.")
    reindex.add_argument(
        "--semantic",
        action="store_true",
        help="Explicitly download/cache the pinned multilingual model and atomically rebuild semantic vectors.",
    )

    subparsers.add_parser("init", help="Interactively seed self identity and optional contact data.")

    demo = subparsers.add_parser("demo", help="Seed a dedicated fictional demonstration database.")
    demo.add_argument("--reset", action="store_true", help="Replace only the dedicated demo database.")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, dispatch to the matching subcommand, return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "db-path":
        return _cmd_db_path(args)
    if args.command == "demo":
        return _cmd_demo(args)

    ctx = _open_context(args.db)
    try:
        if args.command == "list":
            return _cmd_list(ctx, args)
        if args.command == "search":
            return _cmd_search(ctx, args)
        if args.command == "show":
            return _cmd_show(ctx, args)
        if args.command == "export":
            return _cmd_export(ctx, args)
        if args.command == "export-vault":
            return _cmd_export_vault(ctx, args)
        if args.command == "edit":
            return _cmd_edit(ctx, args)
        if args.command == "add-alias":
            return _cmd_add_alias(ctx, args)
        if args.command == "set":
            return _cmd_set(ctx, args)
        if args.command == "delete":
            return _cmd_delete(ctx, args)
        if args.command == "relationship-types":
            return _cmd_relationship_types(ctx, args)
        if args.command == "normalize-relationships":
            return _cmd_normalize_relationships(ctx, args)
        if args.command == "sync-log":
            return _cmd_sync_log(ctx, args)
        if args.command == "reindex":
            return _cmd_reindex(ctx, args)
        if args.command == "init":
            return _cmd_init(ctx)
        parser.error(f"unknown command: {args.command}")
        return 2
    finally:
        ctx.conn.close()


def _cmd_db_path(args: argparse.Namespace) -> int:
    if args.verbose:
        for line in describe_resolution(args.db):
            print(line)
    else:
        print(resolve_db_path(args.db))
    return 0


def _cmd_init(ctx: CliContext) -> int:
    """Run safe, additive onboarding through existing audited application use cases."""
    target, fresh, exit_code = _preflight_init(ctx)
    if exit_code != 0:
        return exit_code
    if not fresh:
        assert target is not None
        answer = input(f"Add onboarding data to existing self {target.canonical_name} ({target.id})? [y/N] ")
        if answer.strip().casefold() not in {"y", "yes"}:
            print("Aborted.")
            return 0
        name = target.canonical_name
    else:
        name = " ".join(input("Canonical name: ").split())
        if not name:
            print("Canonical name must not be blank.", file=sys.stderr)
            return 2

    aliases = _prompt_email_aliases()
    if aliases is None:
        return 2
    if not _preflight_init_aliases(ctx, target, aliases):
        return 1
    raw_vcard_path = input("vCard path (leave blank to skip): ").strip()
    vcard_path: Path | None = None
    if raw_vcard_path:
        vcard_path = _preflight_vcard_path(raw_vcard_path)
        if vcard_path is None:
            return 1

    remember = RememberPerson(ctx.repo, ctx.repo, ctx.audit, ctx.clock)
    try:
        remembered = remember.execute(
            RememberPersonInput(
                name=name,
                aliases=aliases,
                is_self=True,
                source="cli/init",
            )
        )
    except (AmbiguousPersonError, SelfAlreadyExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target = remembered.person
    print(f"Self identity {'created' if remembered.created else 'updated'}: {target.canonical_name} ({target.id}).")

    if vcard_path:
        import_exit = _run_init_vcard_import(ctx, vcard_path, target)
        if import_exit != 0:
            return import_exit

    philosophy = input("Communication philosophy (optional, one line): ").strip()
    if philosophy:
        if "\n" in philosophy or "\r" in philosophy:
            print("Communication philosophy must be one line.", file=sys.stderr)
            return 2
        SetCommunicationPhilosophy(ctx.preferences, ctx.audit, ctx.clock).execute(
            SetCommunicationPhilosophyInput(text=philosophy, source="cli/init")
        )
        print("Communication philosophy recorded.")
    print("Onboarding complete.")
    return 0


def _preflight_init(ctx: CliContext) -> tuple[Person | None, bool, int]:
    people = ctx.repo.list_people(include_deleted=True)
    if not people:
        return None, True, 0
    self_people = [person for person in people if person.deleted_at is None and person.is_self]
    if len(self_people) != 1:
        print("Cannot continue: non-empty state must contain exactly one active self person.", file=sys.stderr)
        return None, False, 1
    target = self_people[0]
    matches = ctx.repo.find_by_normalized_name(normalize_name(target.canonical_name))
    if len(matches) != 1 or matches[0].id != target.id:
        print("Cannot continue: the existing self identity is ambiguous.", file=sys.stderr)
        return None, False, 1
    return target, False, 0


def _prompt_email_aliases() -> list[AliasInput] | None:
    raw = input("Email handles (comma-separated, optional): ")
    aliases: list[AliasInput] = []
    seen: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        address = normalize_name(item)
        local, separator, domain = address.partition("@")
        if not separator or not local or not domain or "@" in domain or any(char.isspace() for char in address):
            print("Email handles must be valid comma-separated addresses.", file=sys.stderr)
            return None
        if address not in seen:
            seen.add(address)
            aliases.append(AliasInput(value=address, kind=AliasKind.HANDLE))
    return aliases


def _preflight_init_aliases(ctx: CliContext, target: Person | None, aliases: list[AliasInput]) -> bool:
    for alias in aliases:
        matches = ctx.repo.find_by_normalized_name(normalize_name(alias.value))
        if not matches:
            continue
        if target is not None and len(matches) == 1 and matches[0].id == target.id:
            continue
        print(f"Cannot continue: email handle {alias.value} identifies another or ambiguous person.", file=sys.stderr)
        return False
    return True


def _preflight_vcard_path(raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser().absolute()
    try:
        if not path.is_file():
            raise OSError("path is not a readable file")
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        print(f"Cannot read vCard file: {exc}", file=sys.stderr)
        return None
    return path


def _run_init_vcard_import(ctx: CliContext, path: Path, self_person: Person) -> int:
    handles = [alias.value for alias in self_person.aliases if alias.kind == AliasKind.HANDLE]
    if not handles:
        print(
            "Warning: self has no email handle; email-based self-card exclusion is unavailable.",
            file=sys.stderr,
        )
    staging = SqliteImportStagingStore(ctx.conn)
    records = SqliteRecordStore(ctx.conn)
    importer = ImportContent(ctx.repo, ImportExtractorRouter(), staging, ctx.clock)
    reviewer = ReviewImport(staging)
    committer = CommitImport(
        ctx.repo,
        staging,
        RememberPerson(ctx.repo, ctx.repo, ctx.audit, ctx.clock),
        RecordInteraction(ctx.repo, records, ctx.audit, ctx.clock),
        SetAffiliation(ctx.repo, SqliteOrganizationStore(ctx.conn), records, ctx.audit, ctx.clock),
        RecordFact(ctx.repo, records, ctx.audit, ctx.clock),
    )
    try:
        batch = importer.execute("vcard", path=str(path))
        review = reviewer.execute(batch.batch_id)
    except ImportPipelineError as exc:
        if exc.code == "no_candidates":
            print("No external vCard candidates found.")
            return 0
        print(f"vCard import failed: {exc}", file=sys.stderr)
        return 1
    except (ImportExtractionError, OSError) as exc:
        print(f"vCard import failed: {exc}", file=sys.stderr)
        return 1
    _print_import_review(review.candidates)
    selected = [
        candidate_id.strip()
        for candidate_id in input("Candidate IDs to accept (comma-separated): ").split(",")
    ]
    accepted_ids = list(dict.fromkeys(candidate_id for candidate_id in selected if candidate_id))
    known_ids = {row.id for row in review.candidates}
    unknown_ids = sorted(set(accepted_ids) - known_ids)
    if unknown_ids:
        print("Unknown candidate IDs: " + ", ".join(unknown_ids), file=sys.stderr)
        return 2
    try:
        result = committer.execute(batch.batch_id, accepted_ids)
    except ImportPipelineError as exc:
        print(f"vCard commit failed: {exc}", file=sys.stderr)
        return 1
    print(f"Committed {len(result.committed_ids)} import candidates; {len(result.unresolved_ids)} unresolved.")
    return 0


def _print_import_review(rows: list[ImportReviewRow]) -> None:
    print("Import candidates:")
    person_names = {
        row.id: str(row.candidate["name"])
        for row in rows
        if row.candidate["type"] == "person"
    }
    for row in rows:
        candidate_id = row.id
        candidate = row.candidate
        candidate_type = candidate["type"]
        if candidate_type == "person":
            detail = candidate["name"]
        elif candidate_type == "affiliation":
            detail = f"{candidate['role']} at {candidate['org']} — {_import_owner(candidate, person_names)}"
        elif candidate_type == "fact":
            detail = f"{candidate['predicate']}={candidate['value']} — {_import_owner(candidate, person_names)}"
        else:
            detail = "summary-only interaction"
        print(f"  {candidate_id}  {candidate_type}  {detail}")


def _import_owner(candidate: dict[str, object], person_names: dict[str, str]) -> str:
    person_candidate_id = str(candidate["person_candidate_id"])
    person_name = str(person_names.get(person_candidate_id, "unknown person"))
    return f"{person_name} ({person_candidate_id})"


def _cmd_demo(args: argparse.Namespace) -> int:
    """Create only the dedicated fictional demo database."""
    demo_path = _demo_db_path()
    if _existing_demo_files(demo_path) and not args.reset:
        print(f"Demo database already exists: {demo_path}. Use --reset to replace it.", file=sys.stderr)
        return 1
    if args.reset:
        _remove_demo_files(demo_path)
    ctx = _open_context_path(demo_path)
    try:
        people = _seed_demo(ctx)
    finally:
        ctx.conn.close()
    _print_demo_instructions(demo_path, people)
    return 0


def _demo_db_path(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    data_home = values.get("XDG_DATA_HOME")
    if data_home:
        base = Path(data_home).expanduser()
    else:
        home = values.get("HOME")
        base = Path(home).expanduser() / ".local" / "share" if home else Path.home() / ".local" / "share"
    return Path(os.path.abspath(base / "people-context" / _DEMO_FILENAME))


def _existing_demo_files(demo_path: Path) -> list[Path]:
    return [path for path in _demo_files(demo_path) if os.path.lexists(path)]


def _remove_demo_files(demo_path: Path) -> None:
    for path in _demo_files(demo_path):
        path.unlink(missing_ok=True)


def _demo_files(demo_path: Path) -> tuple[Path, Path, Path]:
    return demo_path, Path(f"{demo_path}-wal"), Path(f"{demo_path}-shm")


def _seed_demo(ctx: CliContext) -> dict[str, Person]:
    records = SqliteRecordStore(ctx.conn)
    organizations = SqliteOrganizationStore(ctx.conn)
    relationships = ctx.relationship_store
    vocabulary = ctx.relationship_vocabulary
    if relationships is None or vocabulary is None:
        raise RuntimeError("demo requires relationship adapters")
    remember = RememberPerson(ctx.repo, ctx.repo, ctx.audit, ctx.clock)
    set_affiliation = SetAffiliation(ctx.repo, organizations, records, ctx.audit, ctx.clock)
    record_fact = RecordFact(ctx.repo, records, ctx.audit, ctx.clock)
    record_interaction = RecordInteraction(ctx.repo, records, ctx.audit, ctx.clock)
    set_relationship = SetRelationship(ctx.repo, relationships, ctx.audit, ctx.clock, vocabulary)

    people: dict[str, Person] = {}
    for seed in DEMO_PEOPLE:
        aliases = [AliasInput(value=handle, kind=AliasKind.HANDLE) for handle in seed.handles]
        people[seed.key] = remember.execute(
            RememberPersonInput(
                name=seed.name,
                aliases=aliases,
                summary=seed.summary,
                is_self=seed.is_self,
                source="demo",
            )
        ).person
    for seed in DEMO_AFFILIATIONS:
        set_affiliation.execute(
            SetAffiliationInput(
                person_id=people[seed.person_key].id,
                org=seed.organization,
                role=seed.role,
                source="demo",
            )
        )
    for seed in DEMO_FACTS:
        record_fact.execute(
            RecordFactInput(
                person_id=people[seed.person_key].id,
                predicate=seed.predicate,
                value=seed.value,
                valid_from=seed.valid_from,
                source="demo",
            )
        )
    for seed in DEMO_INTERACTIONS:
        record_interaction.execute(
            RecordInteractionInput(
                summary=seed.summary,
                participant_ids=[people[key].id for key in seed.participant_keys],
                occurred_at=seed.occurred_at,
                channel=seed.channel,
                source="demo",
            )
        )
    for seed in DEMO_RELATIONSHIPS:
        set_relationship.execute(
            SetRelationshipInput(
                subject_id=people[seed.subject_key].id,
                object_id=people[seed.object_key].id,
                type=seed.relationship_type,
                source="demo",
            )
        )
    return people


def _print_demo_instructions(demo_path: Path, people: dict[str, Person]) -> None:
    print(f"Demo database: {demo_path}")
    print(f"Start MCP server: people-context-mcp --db {shlex.quote(str(demo_path))}")
    print(f'resolve_person {{"query": "{people["amina"].canonical_name}"}}')
    print(f'get_relationship_graph {{"person_id": "{people["amina"].id}", "depth": 2}}')
    print(
        f'find_connection {{"person_a": "{people["self"].id}", '
        f'"person_b": "{people["sofia"].id}"}}'
    )


def _cmd_list(ctx: CliContext, args: argparse.Namespace) -> int:
    people = ctx.repo.list_people(include_deleted=args.all, limit=args.limit)
    if not people:
        print("No people found.")
        return 0
    _print_table(
        ["ID", "NAME", "ALIASES", "SUMMARY"],
        [_list_row(person) for person in people],
    )
    return 0


def _list_row(person: Person) -> tuple[str, str, str, str]:
    name = person.canonical_name + (" [deleted]" if person.deleted_at else "")
    return (person.id, name, str(len(person.aliases)), _truncate(person.summary or "", _SUMMARY_WIDTH))


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True)))
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))


def _cmd_search(ctx: CliContext, args: argparse.Namespace) -> int:
    results = SearchPeople(ctx.repo).execute(args.query, limit=args.limit)
    if not results:
        print(f"No matches for '{args.query}'.")
        return 0
    for candidate in results:
        print(f"{candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})  {candidate.match_reason}")
    return 0


def _cmd_show(ctx: CliContext, args: argparse.Namespace) -> int:
    person, exit_code = _resolve_person(ctx, args.person)
    if person is None:
        return exit_code
    context = GetPersonContext(ctx.repo, ctx.context_reader, ctx.clock).execute(person.id, include_sensitive=True)
    _print_context(context)
    return 0


def _print_context(context: PersonContextResult) -> None:
    identity = context.identity
    if identity is None:
        return
    print(f"{identity.canonical_name} ({identity.id})")
    print(f"  self: {identity.is_self}")
    print(f"  summary: {identity.summary or '(none)'}")
    if identity.aliases:
        print("  aliases:")
        for alias in identity.aliases:
            print(f"    - {alias}")
    else:
        print("  aliases: (none)")
    _print_section(
        "relationships",
        [
            f"{record.display_type}: {record.other_person_name} ({record.other_person_id})"
            + (f" — {record.relationship.label}" if record.relationship.label else "")
            for record in context.relationships
        ],
    )
    _print_section(
        "affiliations",
        [f"{record.affiliation.role} at {record.organization_name}" for record in context.affiliations],
    )
    _print_section("facts", [f"{fact.predicate}: {fact.value}" for fact in context.facts])
    _print_section(
        "interactions",
        [
            f"{interaction.occurred_at.date().isoformat()}: {interaction.summary}"
            for interaction in context.interactions
        ],
    )
    _print_section("communication reminders", [reminder.text for reminder in context.reminders])


def _print_section(title: str, items: list[str]) -> None:
    print(f"  {title}:")
    if not items:
        print("    (none)")
        return
    for item in items:
        print(f"    - {item}")


def _cmd_export(ctx: CliContext, args: argparse.Namespace) -> int:
    document = ExportData(ctx.export_reader, ctx.clock).execute().model_dump(mode="json")
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.output:
        # The export contains the complete dataset; keep it owner-readable only.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


def _cmd_export_vault(ctx: CliContext, args: argparse.Namespace) -> int:
    if ctx.vault_reader is None:
        raise RuntimeError("export-vault requires a vault reader")
    try:
        result = ExportVault(ctx.vault_reader, FileSystemVaultWriter()).execute(
            args.output,
            include_sensitive=args.include_sensitive,
        )
    except VaultSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Exported {result.people} people and {result.organizations} organizations "
        f"to {result.output} ({result.files} files)."
    )
    return 0


def _resolve_person(ctx: CliContext, reference: str) -> tuple[Person | None, int]:
    person = ctx.repo.get(reference)
    if person is not None and person.deleted_at is None:
        return person, 0
    result = ResolvePerson(ctx.repo, ctx.context_reader, ctx.clock).execute(reference)
    if not result.candidates:
        print(f"No person found matching '{reference}'.", file=sys.stderr)
        return None, 1
    if result.ambiguous:
        print(f"Ambiguous match for '{reference}'; candidates:", file=sys.stderr)
        for candidate in result.candidates:
            print(f"  {candidate.score:.2f}  {candidate.canonical_name}  ({candidate.person_id})", file=sys.stderr)
        return None, 2
    resolved = ctx.repo.get(result.candidates[0].person_id)
    if resolved is None or resolved.deleted_at is not None:
        print(f"No person found matching '{reference}'.", file=sys.stderr)
        return None, 1
    return resolved, 0


def _cmd_edit(ctx: CliContext, args: argparse.Namespace) -> int:
    if args.name is None and args.summary is None:
        print("edit requires at least one of --name or --summary.", file=sys.stderr)
        return 2
    person, exit_code = _resolve_person(ctx, args.person)
    if person is None:
        return exit_code
    try:
        updated = EditPerson(ctx.repo, ctx.repo, ctx.audit, ctx.clock).execute(
            EditPersonInput(person_id=person.id, name=args.name, summary=args.summary)
        )
    except PersonNameCollisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Updated {updated.canonical_name} ({updated.id}).")
    return 0


def _cmd_add_alias(ctx: CliContext, args: argparse.Namespace) -> int:
    person, exit_code = _resolve_person(ctx, args.person)
    if person is None:
        return exit_code
    updated = AddAlias(ctx.repo, ctx.repo, ctx.audit, ctx.clock).execute(
        AddAliasInput(
            person_id=person.id,
            value=args.value,
            kind=AliasKind(args.kind),
            lang=args.lang,
            script=args.script,
            source="cli",
        )
    )
    print(f"Alias recorded for {updated.canonical_name} ({updated.id}).")
    return 0


def _cmd_set(ctx: CliContext, args: argparse.Namespace) -> int:
    if args.key != PREF_COMMUNICATION_PHILOSOPHY:
        print(f"Unsupported preference key: {args.key}", file=sys.stderr)
        return 2
    SetCommunicationPhilosophy(ctx.preferences, ctx.audit, ctx.clock).execute(
        SetCommunicationPhilosophyInput(text=args.value, source="cli")
    )
    print(f"Set {args.key}.")
    return 0


def _cmd_delete(ctx: CliContext, args: argparse.Namespace) -> int:
    person, exit_code = _resolve_person(ctx, args.person)
    if person is None:
        return exit_code
    preview = PreviewForget(ctx.repo, ctx.lifecycle).execute(person.id)
    print(f"Delete {preview.canonical_name} ({preview.person_id}) permanently?")
    for entity_type, count in preview.deleted.items():
        if count:
            print(f"  {entity_type}: {count}")
    if not args.yes and input("Proceed? [y/N] ").strip().casefold() not in {"y", "yes"}:
        print("Aborted.")
        return 0
    Forget(ctx.repo, ctx.lifecycle, ctx.clock, ctx.audit).execute(person.id, "person", source="cli")
    print("Deleted.")
    return 0


def _cmd_relationship_types(ctx: CliContext, args: argparse.Namespace) -> int:
    if ctx.relationship_vocabulary is None:
        raise RuntimeError("relationship-types requires a vocabulary adapter")
    if args.relationship_types_command == "add":
        try:
            rows = AddRelationshipType(
                ctx.relationship_vocabulary,
                ctx.relationship_vocabulary,
                ctx.audit,
                ctx.clock,
            ).execute(
                AddRelationshipTypeInput(
                    type=args.type,
                    category=args.category,
                    inverse=args.inverse,
                    symmetric=args.symmetric,
                    synonyms=args.synonym,
                )
            )
        except (RelationshipTypeAlreadyExistsError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Added relationship vocabulary: " + ", ".join(row.type for row in rows))
        return 0
    rows = ctx.relationship_vocabulary.list_types()
    _print_table(
        ["TYPE", "INVERSE", "SYMMETRIC", "CATEGORY", "CANONICAL", "SYNONYMS"],
        [
            (
                row.type,
                row.inverse or "-",
                "yes" if row.symmetric else "no",
                row.category,
                "yes" if row.canonical else "no",
                ", ".join(row.synonyms) or "-",
            )
            for row in rows
        ],
    )
    print("\nUncategorized types in use:")
    uncategorized = ctx.relationship_vocabulary.list_uncategorized_types()
    if not uncategorized:
        print("  (none)")
    else:
        for type_name in uncategorized:
            print(f"  - {type_name}")
    return 0


def _cmd_normalize_relationships(ctx: CliContext, args: argparse.Namespace) -> int:
    if ctx.relationship_store is None or ctx.relationship_vocabulary is None:
        raise RuntimeError("normalize-relationships requires relationship adapters")
    result = NormalizeRelationships(
        ctx.relationship_store,
        ctx.relationship_vocabulary,
        ctx.audit,
        ctx.clock,
    ).execute(apply=args.apply)
    if not result.changes:
        print("No relationship normalization changes.")
        return 0
    print("Applied relationship normalization:" if result.applied else "Dry run; relationship normalization would:")
    for change in result.changes:
        if change.action == "update" and change.after is not None:
            print(
                f"  update {change.relationship_id}: "
                f"{change.before.subject_id} {change.before.type} {change.before.object_id} -> "
                f"{change.after.subject_id} {change.after.type} {change.after.object_id}"
            )
        else:
            print(f"  merge {change.relationship_id} into {change.merged_into}")
    if not result.applied:
        print("Run again with --apply to execute these audited rewrites.")
    return 0


def _cmd_sync_log(ctx: CliContext, args: argparse.Namespace) -> int:
    if ctx.changelog is None:
        raise RuntimeError("sync-log requires a changelog adapter")
    entries = ctx.changelog.list_entries(limit=args.limit, entity_id=args.entity)
    if not entries:
        print("No changelog entries.")
        return 0
    for entry in entries:
        fields = ",".join(entry.changed_fields) if entry.changed_fields else "-"
        print(
            f"{entry.op_kind}  {entry.entity_type}:{entry.entity_id}  device={entry.device_id}  "
            f"hlc={entry.hlc_physical_ms}:{entry.hlc_logical}  fields={fields}"
        )
        if args.payloads:
            payload = json.dumps(entry.payload, ensure_ascii=False, sort_keys=True)
            print(f"  payload={payload}")
    return 0


def _cmd_reindex(ctx: CliContext, args: argparse.Namespace) -> int:
    result = ReindexPeople(ctx.repo).execute()
    print(f"Reindexed {result.people} people and {result.names} names.")
    if not args.semantic:
        return 0
    print(f"Semantic model: {MODEL_ID}")
    print(f"Pinned artifact: {MODEL_URL}")
    print(f"Download size: {MODEL_DOWNLOAD_SIZE}")
    print(f"Cache directory: {semantic_cache_dir()}")
    try:
        provider = download_embedding_provider()
        semantic_result = ReindexSemantic(
            SqliteSemanticDocumentReader(ctx.conn),
            provider,
            create_sqlite_vector_index(ctx.conn),
        ).execute()
    except Exception as exc:  # noqa: BLE001 - preserve prior index on package, download, or embedding failures
        print(f"Semantic reindex failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Reindexed {semantic_result.entities} semantic entities "
        f"({semantic_result.people} people, {semantic_result.interactions} interactions) "
        f"with {semantic_result.model_id}."
    )
    return 0
