# M8–M19 pull-request plan

One checklist item is one independently mergeable pull request. Implementers must read the referenced milestone
spec first; the bullets below are binding acceptance criteria and the out-of-scope bullets are hard boundaries.
Check the matching box only in the PR that delivers it.

## Global rules

- `domain` and `app` never import adapters or the MCP SDK.
- Every ordinary durable mutation flows through `audit_mutation`; M11 `BootstrapRestorer` is the sole verbatim
  restore exception and must not mint audit/changelog rows.
- Untrusted file/JSON/CSV/chat/plugin inputs fail closed with explicit schemas, bounded resources, and no shell
  interpretation.
- Migrations are forward-only additive files using the next free number at implementation time.
- Generated dependency state is committed: root dependency changes update `uv.lock`; Node packages commit a
  lockfile and use `npm ci`.
- External Actions and release/validation CLIs are pinned to an immutable commit, digest, or exact reviewed version.
- After M11.2, personal-data text files use the shared atomic private-file writer; do not copy the old
  `O_TRUNC, 0o600` pattern.
- Machine JSON explicitly documented for integrations is versioned and additive under the M12 promise.
- New app behavior gets fake-port and real-SQLite tests; MCP tools get in-memory tests; CLI commands get CLI tests.
- Raw import/transcript/document bodies never become staging metadata, logs, errors, audit/changelog payloads, or
  provenance receipts unless an older importer contract explicitly permits a distilled field.
- Every PR ends with `uv lock --check` where metadata/dependencies changed, `uv run ruff check .`, `uv run mypy`,
  and `uv run pytest -q`; packaging/surface PRs also run `uv build`. Plugin PRs additionally run locked Node
  install/test/build.

| Milestone | Theme | PRs |
|---|---|---:|
| M8 | Distribution & reach | 4 |
| M9 | Cold start & onboarding | 4 |
| M10 | Agent utilization | 3 |
| M11 | Sync bundle & bootstrap restore | 4 |
| M12 | Trust, stability & v1.0 | 4 |
| M13 | Daily utility | 4 |
| M14 | Ecosystem interoperability | 4 |
| M15 | Data quality & credibility | 4 |
| M16 | First-class CLI import | 1 |
| M17 | Agent-assisted extraction | 2 |
| M18 | Provenance, idempotency & evidence | 3 |
| M19 | Consolidation & temporal views | 2 |
| **Total** | | **39** |

## Cross-milestone dependencies

- M9.1 → M14.3: the import router is relocated once.
- M9.4 → M12.3: README demo documentation consumes the packaged demo.
- M10.1 → M13.3: meeting-prep extends the usage skill.
- M11.2 → M13.3/M14.1/M14.2: later file outputs reuse the private atomic writer.
- M12.1 → M12.2: the 1.0 release checklist references the compatibility promise.
- M12.4 → M14.4: the plugin's encrypted toggle invokes the canonical encrypted CLI path.
- M14.1 → M14.4: the plugin consumes `list --json` and `brief --json`.
- M16.1 → M17.2: agent-side strict-candidate CLI staging reuses the `pctx import` group and stable stage/review/commit
  machine surfaces rather than creating a second lifecycle.
- M17.1 → M18.3: durable trait evidence links target observation/interaction records produced by the expanded
  candidate vocabulary.
- M18.1 → M18.2: source-session persistence/idempotency exists before commit provenance and inspection consume it.
- M18.2/M18.3 → M19.1/M19.2: timeline and consolidation can explain source provenance and evidence when present.

## M8 — Distribution & reach

- [x] **M8.1 — Verify zero-clone `uvx` install and lead with it**
  - **Scope:** Verify `uvx --from people-context people-context --help` plus one real stdio round trip from a
    clean environment; reorder README Quick start ahead of tool-install and source-checkout paths.
  - **Acceptance:** primary distribution name is `people-context`; commands and evidence are recorded; no behavior
    or package change.
  - **Out:** Registry, MCPB, editor configs, Docker.

- [x] **M8.2 — MCP Registry and community-directory metadata**
  - **Scope:** Add root `server.json`, packaged `mcp-name:` marker, pinned Registry validation, and a current
    Smithery/PulseMCP/mcp.so/Glama submission matrix plus any required static in-repo metadata files.
  - **Acceptance:** server/package versions match; stdio package transport is schema-valid; namespace decision
    recorded; each directory's primary docs and manual-vs-repository path are explicit; validators are pinned.
  - **Out:** live publication/approval and MCPB package entry.

- [x] **M8.3 — Native-UV MCPB bundle and editor configs**
  - **Scope:** Add MCPB manifest/project/entry point, exact reviewed build tooling, release attachment, and
    Cursor/Windsurf/VS Code `uvx` snippets.
  - **Acceptance:** `server.type="uv"`; semantic version/dependency pin match release; schema `manifest_version`
    independent; archive inspected; clean-machine Desktop smoke test; local-permission warning.
  - **Out:** Docker and stable MCPB Registry URL/digest entry.

- [x] **M8.4 — Optional non-root Docker image and GHCR release**
  - **Scope:** Multi-stage Dockerfile, `.dockerignore`, tag-triggered GHCR workflow, README volume/env usage.
  - **Acceptance:** pinned base digest/Actions; non-root stdio runtime; explicit mounted DB; no surprise runtime
    network; image help and real stdio smoke test.
  - **Out:** HTTP-default image.

## M9 — Cold start & onboarding

- [x] **M9.1 — Relocate import router without dropping `mbox`**
  - **Scope:** Move explicit dispatch to `adapters/importers/router.py`.
  - **Acceptance:** `email`/`mbox` → email, `vcard` → vCard, unknown → `invalid_source_type`; preserve `mbox`
    path-only semantics and E2E behavior.
  - **Out:** ICS and LinkedIn implementations.

- [x] **M9.2 — ICS attendee import with explicit time semantics**
  - **Scope:** Add `IcsImportExtractor`, router branch, adapter/app/MCP tests.
  - **Acceptance:** cross-event email dedup/alternate names; self omitted from people/refs; self-only event omitted;
    neutral summary and raw-content sentinels absent everywhere. Support UTC `Z`, resolvable `TZID` normalized to
    UTC, and all-day `VALUE=DATE` as deterministic `00:00:00Z`; skip floating, unknown/ambiguous/nonexistent, or
    malformed starts with stable reasons; never use host local timezone.
  - **Out:** recurrence expansion, DTEND/duration, LinkedIn, onboarding.

- [x] **M9.3 — LinkedIn connections CSV import**
  - **Scope:** Add extractor/router branch for person, optional affiliation, optional connected-date fact.
  - **Acceptance:** preserve earlier sources; tolerate documented header supersets; per-row independence; profile
    URL/free text excluded; normalized-email duplicate rows coalesce, while no-email same-name rows remain distinct;
    stable unique batch refs.
  - **Out:** onboarding commands.

- [x] **M9.4 — Safe `init` and packaged `demo`**
  - **Scope:** Compose existing use cases for onboarding; add deterministic dedicated demo database.
  - **Acceptance:** seed self first with handle aliases; own card/dependants excluded; on a fresh store a no-handle
    same-name card targets self. Non-empty/ambiguous state refuses before mutation unless one self target is
    explicitly confirmed. Demo data ships in wheel, ignores real DB settings, and prints path-targeted server and
    graph-tool examples.
  - **Out:** MCP onboarding and live integrations.

## M10 — Agent utilization

- [x] **M10.1 — Package usage and end-of-session capture skill**
  - **Scope:** Add root usage skill covering resolution first, context vs guidance, strict candidates, elevation
    gates, and review-only capture proposal.
  - **Acceptance:** never commits extracted batches automatically; validation/scripted transcript pass.
  - **Out:** user-invocable workflows and server instructions.

- [x] **M10.2 — Add who/remember/reminders workflows**
  - **Scope:** Three user workflows composing existing tools.
  - **Acceptance:** `who` resolves then reads only when unambiguous; `remember` distinguishes assertion/extraction;
    `reminders` resolves optional person; no elevated tools.
  - **Out:** automatic assertion/extraction heuristics.

- [x] **M10.3 — Name under-used tools in server instructions**
  - **Scope:** Minimal `SERVER_INSTRUCTIONS` string addition.
  - **Acceptance:** no signature/annotation/response change or elevated-tool encouragement; literal tests update.
  - **Out:** tool annotation changes.

## M11 — Sync bundle & bootstrap restore

- [x] **M11.1 — Unbounded changelog read**
  - **Scope:** Widen `list_entries(limit: int | None = 100)`; `None` returns all rows.
  - **Acceptance:** existing default/descending `sync-log` unchanged; deterministic unbounded coverage.
  - **Out:** bundle export.

- [x] **M11.2 — Strict bundle export and private-file primitive**
  - **Scope:** Strict v1 DTOs, single-snapshot reader, `ExportSyncBundle(..., Clock)`, `sync push`, and atomic private
    writer; migrate existing JSON export.
  - **Acceptance:** literal format/version, nested `extra="forbid"`, no restore-input defaults, stable ordering,
    deterministic bytes, unbounded changelog, secure atomic replacement.
  - **File tests:** existing `0644` becomes `0600`; destination symlink target untouched; failed write preserves
    old file.
  - **Out:** restore.

- [x] **M11.3 — Fail-closed bootstrap restore**
  - **Scope:** `BootstrapRestorer`, `RestoreSyncBundle`, `sync pull` with preview/confirmation.
  - **Bundle validation:** wrong format/version, missing/unknown/malformed fields, duplicate ids, invalid origin,
    dangling references, and insufficient watermark fail before preview/prompt.
  - **Baseline target:** under `BEGIN IMMEDIATE`, exactly one active local device, canonical seeded vocabulary only,
    and zero rows in every mutable domain/audit/sync/staging/FTS/optional-vector table. Report non-sensitive counts;
    never clear existing state.
  - **Transaction:** reject device-id collision; reconcile incoming vocabulary; retire imported devices; insert
    domain/audit/changelog verbatim; rebuild FTS; advance local HLC; commit or fully roll back.
  - **Acceptance:** no new audit/changelog rows; per-table baseline, concurrency, and phase-failure tests.
  - **Out:** incremental replay/conflicts/encryption.

- [x] **M11.4 — Multi-device E2E sign-off**
  - **Scope:** A→B stdio/CLI round trip plus B→C historical-device chain.
  - **Acceptance:** portable content/custom vocabulary parity; later B write uses B id and sorts after imports;
    imported devices remain retired/carried forward.
  - **Out:** protocol expansion.

## M12 — Trust, stability & v1.0

- [x] **M12.1 — Publish compatibility promise**
  - **Scope:** Add/link `docs/compatibility.md`.
  - **Acceptance:** additive MCP/stable JSON, forward-only DB, compatible CLI defaults; vault Markdown not frozen;
    no invented deprecation window.
  - **Out:** release bump, encryption, threat comparison.

- [ ] **M12.2 — Synchronize 1.0 server metadata and lock**
  - **Scope:** Root project, Registry, MCPB, `uv.lock`, classifier, release docs.
  - **Acceptance:** five server semantic values equal `1.0.0`; MCPB schema independent; Registry entry by identifier;
    lock root version matches and `uv lock --check` passes. Shim/plugin version domains remain independent unless
    intentionally published, then synchronize internally.
  - **Out:** tag/release and SQLCipher.

- [x] **M12.3 — Dated threat comparison and README demo**
  - **Scope:** Primary-source “as of” local-vs-cloud comparison; packaged-demo walkthrough.
  - **Acceptance:** storage, breach/legal exposure, offline operation, deletion; factual language and valid links.
  - **Out:** telemetry or demo behavior changes.

- [x] **M12.4 — Opt-in SQLCipher with locked dependency state**
  - **Scope:** encrypted extra, `open_encrypted_db`, server/global CLI flag, `uv.lock`, tests/docs.
  - **Acceptance:** key before schema/migrations; non-empty env key only; no fallback/leakage; correct/wrong/plain
    reader/WAL sentinel tests; supported-platform wheel probe; locked all-extras CI actually installs it.
  - **Out:** default encryption, rotation, keychain, multi-key.

## M13 — Daily utility

- [x] **M13.1 — Stale relationships MCP/CLI report**
  - **Scope:** `GetStaleRelationships(RecencyReader, Clock)`, SQLite aggregate query, read-only MCP tool, CLI.
  - **Acceptance:** one row/person/all active categories; only ordinary interactions; app computes signed days via
    fakeable clock; `threshold_days 0..36500`, `limit 1..100`; null first, stable sort, truncation; future timestamps
    are not stale; SQL sensitivity tests.
  - **Out:** health score/elevated variant.

- [x] **M13.2 — Upcoming dates MCP/CLI report**
  - **Scope:** `ListUpcomingDates(PersonContextReader, ListReminders, PersonReader, Clock)`.
  - **Acceptance:** `window_days 0..366`; inclusive window; annual full/partial birthdays; real leap days; stored
    reminder date component; missing/deleted skipped; elevated facts invisible to counts.
  - **Out:** additional predicates/elevated variant.

- [x] **M13.3 — Meeting-prep skill and private reminder ICS export**
  - **Scope:** Extend skill; deterministic `reminders-ics` using M11 writer.
  - **Acceptance:** only aware `due_at`/`created_at`; canonical UTC/folding/escaping; `skipped_undated` and
    `skipped_naive_datetime` omit rows without guessing timezone; supported RRULEs; `recurrence_omitted` counts
    exported reminders with omitted unsupported RRULE; deterministic/secure file tests.
  - **Out:** write-contract timezone enforcement, VEVENT, third-party push.

- [x] **M13.4 — Deterministic local changelog watch**
  - **Scope:** Add ascending `list_entries_after`; JSONL polling CLI.
  - **Acceptance:** interval `0.1..3600`, bounded batch `1..1000`; default starts at current latest without replay;
    `--from-start` replays all; full cursor/multi-batch advancement; testable poll/sleep seam; local stdout only.
  - **Out:** daemon/network sink; future `--once`.

## M14 — Ecosystem interoperability

- [x] **M14.1 — Stable person brief and person-index JSON**
  - **Scope:** Compose brief, Markdown/versioned JSON, `list --json`, private file output.
  - **Acceptance:** all reminder kinds; sensitive flag widens context only, guidance stays ordinary; disclosure
    labels; deterministic ordering and secure overwrite tests.
  - **Out:** new MCP tool/guidance change.

- [x] **M14.2 — Deterministic vCard writer**
  - **Scope:** Typed port/DTOs, app projection, writer, CLI/private file.
  - **Acceptance:** `FN` canonical; non-heuristic one-component `N`; active/sensitivity filtering; one affiliation;
    one full birthday; omitted-valid/skipped-partial/skipped-unparseable counts; 3.0/4.0 unchanged-importer roundtrip.
  - **Out:** CardDAV, multi-value encoding, partial-birthday importer normalization.

- [x] **M14.3 — Outlook and WhatsApp extractors**
  - **Scope:** New extractors; widen Protocol/router/all implementations with `self_names`/`self_sender`.
  - **Acceptance:** preserve five sources; WhatsApp body absent from outputs/logs/errors; self omitted from people/refs;
    self-only day no interaction; seven-source matrix/E2E.
  - **Out:** Signal and candidate-schema self field.

- [x] **M14.4 — Safe read-only Obsidian plugin and mirror**
  - **Scope:** Package/lockfile, CLI bridge, panes, tests, deterministic distribution workflow.
  - **Execution:** stable ids; `spawn`/`execFile`, arg arrays, `shell:false`; no command/freeform args; timeout,
    cancellation, output bounds, metacharacter tests.
  - **Settings/build:** typed executable/DB/encrypted/refresh; inherited key never stored; missing key no fallback;
    `npm ci`, reproducible artifacts, desktop-only manifest.
  - **Out:** writes/raw SQLite/community submission.

## M15 — Data quality & credibility

- [x] **M15.1 — Deterministic doctor findings**
  - **Scope:** `CurationReader`, SQLite queries, app report, CLI; optional next-free index.
  - **Acceptance:** duplicate handle/alias, contradictory fact, soft-deleted references; handle precedence;
    `ValidityPeriod.overlaps` parity; report-only/exit zero. JSON actions are structured id-based argv or MCP tool
    arguments, never shell names; versioned stable JSON.
  - **Out:** interactive repair/MCP findings tool.

- [x] **M15.2 — Aggregate-only stats report**
  - **Scope:** `StatsReader`, aggregate adapter, app redaction, CLI.
  - **Acceptance:** no record text/device names/paths from adapter; explicit gate booleans/path; redacted default;
    versioned JSON; no server/network probe; main+WAL+SHM bytes; in-memory/unavailable explicit null state.
  - **Out:** doctor/telemetry.

- [x] **M15.3 — Additive transliteration match detail**
  - **Scope:** Optional descriptive `match_detail`, bilingual fixtures/docs.
  - **Acceptance:** preserve exact reason/score/ranking/ambiguity; canonical wins then stable alias-kind detail; CJK
    and non-CJK bidirectional fixtures.
  - **Out:** fuzzy cross-script/ranking change.

- [x] **M15.4 — Reproducible eval harness and use-case gallery**
  - **Scope:** Fictional fixtures, fixed tasks/rubrics, with/without MCP runs, dated docs, recipes.
  - **Acceptance:** prompts/model ids/harness version; environment-only keys; no real DB; network-free stub dry run;
    production package excludes eval assets.
  - **Out:** hosted telemetry benchmark.

## M16 — First-class CLI import workflow

- [ ] **M16.1 — Expose the existing import lifecycle through `pctx`**
  - **Scope:** Add `pctx import stage SOURCE PATH`, `import review`, and `import commit --all|--accept`, all with
    stable `--json`; support exactly the seven existing router sources; refactor shared vCard onboarding rendering /
    selection only enough to avoid a second CLI implementation.
  - **Acceptance:** stage never commits; `--all` and repeatable canonical `--accept` are mutually exclusive and one
    is required; no second confirmation prompt; stable v1 batch/review/commit JSON documents are deterministic;
    errors/raw-content sentinels stay off stdout/stderr; installed CLI stage→review→commit→list E2E passes; existing
    `pctx init` semantics remain unchanged.
  - **Out:** new importers/candidate types, stdin/raw-content import, `stage_candidates` CLI, batch management,
    embedded models/network, general CLI/MCP parity.

## M17 — Agent-assisted knowledge extraction

- [ ] **M17.1 — Expand strict candidates to observation, trait, and relationship**
  - **Scope:** Add strict candidate models/staging dependency rewriting and `CommitImport` support through existing
    `RecordObservation`, `RecordTrait`, and `SetRelationship` use cases.
  - **Acceptance:** staged traits require explicit confidence and non-blank concise evidence note; relationship
    candidates use batch-local person refs and canonical M7 vocabulary semantics; people commit/resolve before
    dependants; unresolved refs remain unresolved; all durable writes retain normal audit/changelog/provenance;
    existing candidate types/envelopes remain additive-compatible.
  - **Out:** source receipts/evidence tables, raw-text parsing, LLM/runtime dependency, automatic commit/confidence.

- [ ] **M17.2 — Add agent candidate CLI and unstructured-source workflow**
  - **Scope:** Add `pctx import stage-candidates --source SOURCE --input PATH|- [--json]` over the same strict models
    as MCP and extend the packaged agent skill for transcript/note extraction.
  - **Acceptance:** input is candidate JSON, never raw transcript; stdin/file fail closed on malformed/extra fields;
    workflow distinguishes explicit fact vs observation vs inferred trait, uses candidate matching for identities,
    discourages speculative sensitive inference/verbatim evidence, stages only, and requires explicit later commit;
    human/MCP paths remain usable without CLI.
  - **Out:** model invocation, prompt storage, transcript persistence, automatic review/commit, source idempotency.

## M18 — Provenance, idempotency & evidence

- [ ] **M18.1 — Add source receipts and duplicate-safe staging**
  - **Scope:** Add the next-free migration and ports/app/SQLite support for minimal source sessions/receipts; hash
    exact bytes for M16 structured files, associate one staging batch with one source session, and extend M11
    bootstrap bundles additively with incomplete source-session staging state.
  - **Acceptance:** receipt stores bounded kind/optional label/digest/timestamps/batch metadata only—no body/default
    absolute path; default source-kind+digest claim + source-session insert + batch/candidate staging publish
    atomically under a uniqueness mechanism so concurrent importers create one canonical batch; intentional forced
    reprocessing is explicitly distinct; agent candidate staging may supply optional digest metadata; no-digest
    staging still works; staged/partially committed bundles restore with their reviewable staging rows and older
    bundles without M18 fields remain valid.
  - **Out:** semantic candidate dedup, commit provenance propagation, trait evidence, source rollback, folder watch,
    incremental peer replication of staging state.

- [ ] **M18.2 — Propagate source provenance and expose source inspection**
  - **Scope:** Add a durable record-to-source-session association for every batch-committed record while preserving
    existing `Provenance.session` values; add local `sources` / `source show` inspection with versioned JSON if
    agent-facing.
  - **Acceptance:** each committed candidate has one durable source-session anchor without raw content; existing
    message/event-id `Provenance.session` semantics remain unchanged; association writes are atomic with record
    commit and sync/bootstrap-aware; inspection shows ids, kind/digest/status/batch and derived record summaries/ids
    only; no raw source/path leak; partial/idempotent commits remain understandable.
  - **Out:** trait evidence links, source rollback/delete cascade, document retrieval, confidence recomputation.

- [ ] **M18.3 — Ground traits in durable evidence records**
  - **Scope:** Add the next-free additive trait-evidence relation and support observation/interaction evidence ids,
    including same-batch staged evidence resolution from M17 candidates.
  - **Acceptance:** only active supported evidence entity types; an observation evidence row must belong to the trait
    subject and an interaction must include that subject; stable id ordering; accepted trait remains unresolved when
    required accepted evidence cannot resolve or resolves to another person's evidence; persisted-id and same-batch
    subject-validation tests; retrieval respects sensitivity and never exposes restricted evidence through a
    visible trait; `evidence_note` remains additive human context; sync/bootstrap/lifecycle tests cover the relation.
  - **Out:** trait→trait evidence, automatic confidence formula, automatic evidence deletion/correction propagation.

## M19 — Knowledge consolidation & temporal views

- [ ] **M19.1 — Add bounded person timeline reads**
  - **Scope:** Add a narrow timeline port/use case, bounded SQLite projection, local `pctx timeline` and ordinary-
    disclosure MCP read over interactions/observations/dated state/traits plus M18 provenance/evidence where useful.
  - **Acceptance:** deterministic effective-time ordering with stable tie-breaks; explicit undated behavior; app and
    underlying traversal/query work bounded; ordinary MCP excludes restricted data; CLI sensitivity opt-in is
    explicit; timeline is read-only projection, not a denormalized event store; stable JSON if documented.
  - **Out:** audit-log dump, new timeline table, consolidation mutation, automatic history rewriting.

- [ ] **M19.2 — Add bounded consolidation context and review-only maintenance workflow**
  - **Scope:** Provide a person-scoped read model exposing duplicate/superseding/reinforcing/contradictory knowledge
    with provenance/evidence and extend the agent skill to propose structured existing-tool actions.
  - **Acceptance:** deterministic bounds/order; M15 doctor remains unchanged; multiple observations can remain
    distinct supporting evidence; agent explains proposals and waits for explicit approval before
    `correct_record`/merge/other mutations; no report/read path writes audit/changelog/domain state; scripted
    approval regression passes.
  - **Out:** autonomous belief updater, confidence-by-count formula, background maintenance daemon, required semantic
    vector clustering, automatic merge/correction.
