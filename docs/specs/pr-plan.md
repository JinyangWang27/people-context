# M8–M15 pull-request plan

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
- Every PR ends with `uv lock --check` where metadata/dependencies changed, `uv run ruff check .`, and
  `uv run pytest -q`; plugin PRs also run locked Node install/test/build.

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
| **Total** | | **31** |

## Cross-milestone dependencies

- M9.1 → M14.3: the import router is relocated once.
- M9.4 → M12.3: README demo documentation consumes the packaged demo.
- M10.1 → M13.3: meeting-prep extends the usage skill.
- M11.2 → M13.3/M14.1/M14.2: later file outputs reuse the private atomic writer.
- M12.1 → M12.2: the 1.0 release checklist references the compatibility promise.
- M12.4 → M14.4: the plugin's encrypted toggle invokes the canonical encrypted CLI path.
- M14.1 → M14.4: the plugin consumes `list --json` and `brief --json`.

## M8 — Distribution & reach

- [x] **M8.1 — Verify zero-clone `uvx` install and lead with it**
  - **Scope:** Verify `uvx --from people-context people-context-mcp --help` plus one real stdio round trip from a
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

- [ ] **M9.1 — Relocate import router without dropping `mbox`**
  - **Scope:** Move explicit dispatch to `adapters/import_router.py`.
  - **Acceptance:** `email`/`mbox` → email, `vcard` → vCard, unknown → `invalid_source_type`; preserve `mbox`
    path-only semantics and E2E behavior.
  - **Out:** ICS and LinkedIn implementations.

- [ ] **M9.2 — ICS attendee import with explicit time semantics**
  - **Scope:** Add `IcsImportExtractor`, router branch, adapter/app/MCP tests.
  - **Acceptance:** cross-event email dedup/alternate names; self omitted from people/refs; self-only event omitted;
    neutral summary and raw-content sentinels absent everywhere. Support UTC `Z`, resolvable `TZID` normalized to
    UTC, and all-day `VALUE=DATE` as deterministic `00:00:00Z`; skip floating, unknown/ambiguous/nonexistent, or
    malformed starts with stable reasons; never use host local timezone.
  - **Out:** recurrence expansion, DTEND/duration, LinkedIn, onboarding.

- [ ] **M9.3 — LinkedIn connections CSV import**
  - **Scope:** Add extractor/router branch for person, optional affiliation, optional connected-date fact.
  - **Acceptance:** preserve earlier sources; tolerate documented header supersets; per-row independence; profile
    URL/free text excluded; normalized-email duplicate rows coalesce, while no-email same-name rows remain distinct;
    stable unique batch refs.
  - **Out:** onboarding commands.

- [ ] **M9.4 — Safe `init` and packaged `demo`**
  - **Scope:** Compose existing use cases for onboarding; add deterministic dedicated demo database.
  - **Acceptance:** seed self first with handle aliases; own card/dependants excluded; on a fresh store a no-handle
    same-name card targets self. Non-empty/ambiguous state refuses before mutation unless one self target is
    explicitly confirmed. Demo data ships in wheel, ignores real DB settings, and prints path-targeted server and
    graph-tool examples.
  - **Out:** MCP onboarding and live integrations.

## M10 — Agent utilization

- [ ] **M10.1 — Package usage and end-of-session capture skill**
  - **Scope:** Add root usage skill covering resolution first, context vs guidance, strict candidates, elevation
    gates, and review-only capture proposal.
  - **Acceptance:** never commits extracted batches automatically; validation/scripted transcript pass.
  - **Out:** user-invocable workflows and server instructions.

- [ ] **M10.2 — Add who/remember/reminders workflows**
  - **Scope:** Three user workflows composing existing tools.
  - **Acceptance:** `who` resolves then reads only when unambiguous; `remember` distinguishes assertion/extraction;
    `reminders` resolves optional person; no elevated tools.
  - **Out:** automatic assertion/extraction heuristics.

- [ ] **M10.3 — Name under-used tools in server instructions**
  - **Scope:** Minimal `SERVER_INSTRUCTIONS` string addition.
  - **Acceptance:** no signature/annotation/response change or elevated-tool encouragement; literal tests update.
  - **Out:** tool annotation changes.

## M11 — Sync bundle & bootstrap restore

- [ ] **M11.1 — Unbounded changelog read**
  - **Scope:** Widen `list_entries(limit: int | None = 100)`; `None` returns all rows.
  - **Acceptance:** existing default/descending `sync-log` unchanged; deterministic unbounded coverage.
  - **Out:** bundle export.

- [ ] **M11.2 — Strict bundle export and private-file primitive**
  - **Scope:** Strict v1 DTOs, single-snapshot reader, `ExportSyncBundle(..., Clock)`, `sync push`, and atomic private
    writer; migrate existing JSON export.
  - **Acceptance:** literal format/version, nested `extra="forbid"`, no restore-input defaults, stable ordering,
    deterministic bytes, unbounded changelog, secure atomic replacement.
  - **File tests:** existing `0644` becomes `0600`; destination symlink target untouched; failed write preserves
    old file.
  - **Out:** restore.

- [ ] **M11.3 — Fail-closed bootstrap restore**
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

- [ ] **M11.4 — Multi-device E2E sign-off**
  - **Scope:** A→B stdio/CLI round trip plus B→C historical-device chain.
  - **Acceptance:** portable content/custom vocabulary parity; later B write uses B id and sorts after imports;
    imported devices remain retired/carried forward.
  - **Out:** protocol expansion.

## M12 — Trust, stability & v1.0

- [ ] **M12.1 — Publish compatibility promise**
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

- [ ] **M12.3 — Dated threat comparison and README demo**
  - **Scope:** Primary-source “as of” local-vs-cloud comparison; packaged-demo walkthrough.
  - **Acceptance:** storage, breach/legal exposure, offline operation, deletion; factual language and valid links.
  - **Out:** telemetry or demo behavior changes.

- [ ] **M12.4 — Opt-in SQLCipher with locked dependency state**
  - **Scope:** encrypted extra, `open_encrypted_db`, server/global CLI flag, `uv.lock`, tests/docs.
  - **Acceptance:** key before schema/migrations; non-empty env key only; no fallback/leakage; correct/wrong/plain
    reader/WAL sentinel tests; supported-platform wheel probe; locked all-extras CI actually installs it.
  - **Out:** default encryption, rotation, keychain, multi-key.

## M13 — Daily utility

- [ ] **M13.1 — Stale relationships MCP/CLI report**
  - **Scope:** `GetStaleRelationships(RecencyReader, Clock)`, SQLite aggregate query, read-only MCP tool, CLI.
  - **Acceptance:** one row/person/all active categories; only ordinary interactions; app computes signed days via
    fakeable clock; `threshold_days 0..36500`, `limit 1..100`; null first, stable sort, truncation; future timestamps
    are not stale; SQL sensitivity tests.
  - **Out:** health score/elevated variant.

- [ ] **M13.2 — Upcoming dates MCP/CLI report**
  - **Scope:** `ListUpcomingDates(PersonContextReader, ListReminders, PersonReader, Clock)`.
  - **Acceptance:** `window_days 0..366`; inclusive window; annual full/partial birthdays; real leap days; stored
    reminder date component; missing/deleted skipped; elevated facts invisible to counts.
  - **Out:** additional predicates/elevated variant.

- [ ] **M13.3 — Meeting-prep skill and private reminder ICS export**
  - **Scope:** Extend skill; deterministic `reminders-ics` using M11 writer.
  - **Acceptance:** only aware `due_at`/`created_at`; canonical UTC/folding/escaping; `skipped_undated` and
    `skipped_naive_datetime` omit rows without guessing timezone; supported RRULEs; `recurrence_omitted` counts
    exported reminders with omitted unsupported RRULE; deterministic/secure file tests.
  - **Out:** write-contract timezone enforcement, VEVENT, third-party push.

- [ ] **M13.4 — Deterministic local changelog watch**
  - **Scope:** Add ascending `list_entries_after`; JSONL polling CLI.
  - **Acceptance:** interval `0.1..3600`, bounded batch `1..1000`; default starts at current latest without replay;
    `--from-start` replays all; full cursor/multi-batch advancement; testable poll/sleep seam; local stdout only.
  - **Out:** daemon/network sink; future `--once`.

## M14 — Ecosystem interoperability

- [ ] **M14.1 — Stable person brief and person-index JSON**
  - **Scope:** Compose brief, Markdown/versioned JSON, `list --json`, private file output.
  - **Acceptance:** all reminder kinds; sensitive flag widens context only, guidance stays ordinary; disclosure
    labels; deterministic ordering and secure overwrite tests.
  - **Out:** new MCP tool/guidance change.

- [ ] **M14.2 — Deterministic vCard writer**
  - **Scope:** Typed port/DTOs, app projection, writer, CLI/private file.
  - **Acceptance:** `FN` canonical; non-heuristic one-component `N`; active/sensitivity filtering; one affiliation;
    one full birthday; omitted-valid/skipped-partial/skipped-unparseable counts; 3.0/4.0 unchanged-importer roundtrip.
  - **Out:** CardDAV, multi-value encoding, partial-birthday importer normalization.

- [ ] **M14.3 — Outlook and WhatsApp extractors**
  - **Scope:** New extractors; widen Protocol/router/all implementations with `self_names`/`self_sender`.
  - **Acceptance:** preserve five sources; WhatsApp body absent from outputs/logs/errors; self omitted from people/refs;
    self-only day no interaction; seven-source matrix/E2E.
  - **Out:** Signal and candidate-schema self field.

- [ ] **M14.4 — Safe read-only Obsidian plugin and mirror**
  - **Scope:** Package/lockfile, CLI bridge, panes, tests, deterministic distribution workflow.
  - **Execution:** stable ids; `spawn`/`execFile`, arg arrays, `shell:false`; no command/freeform args; timeout,
    cancellation, output bounds, metacharacter tests.
  - **Settings/build:** typed executable/DB/encrypted/refresh; inherited key never stored; missing key no fallback;
    `npm ci`, reproducible artifacts, desktop-only manifest.
  - **Out:** writes/raw SQLite/community submission.

## M15 — Data quality & credibility

- [ ] **M15.1 — Deterministic doctor findings**
  - **Scope:** `CurationReader`, SQLite queries, app report, CLI; optional next-free index.
  - **Acceptance:** duplicate handle/alias, contradictory fact, soft-deleted references; handle precedence;
    `ValidityPeriod.overlaps` parity; report-only/exit zero. JSON actions are structured id-based argv or MCP tool
    arguments, never shell names; versioned stable JSON.
  - **Out:** interactive repair/MCP findings tool.

- [ ] **M15.2 — Aggregate-only stats report**
  - **Scope:** `StatsReader`, aggregate adapter, app redaction, CLI.
  - **Acceptance:** no record text/device names/paths from adapter; explicit gate booleans/path; redacted default;
    versioned JSON; no server/network probe; main+WAL+SHM bytes; in-memory/unavailable explicit null state.
  - **Out:** doctor/telemetry.

- [ ] **M15.3 — Additive transliteration match detail**
  - **Scope:** Optional descriptive `match_detail`, bilingual fixtures/docs.
  - **Acceptance:** preserve exact reason/score/ranking/ambiguity; canonical wins then stable alias-kind detail; CJK
    and non-CJK bidirectional fixtures.
  - **Out:** fuzzy cross-script/ranking change.

- [ ] **M15.4 — Reproducible eval harness and use-case gallery**
  - **Scope:** Fictional fixtures, fixed tasks/rubrics, with/without MCP runs, dated docs, recipes.
  - **Acceptance:** prompts/model ids/harness version; environment-only keys; no real DB; network-free stub dry run;
    production package excludes eval assets.
  - **Out:** hosted telemetry benchmark.
