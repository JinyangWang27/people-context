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

- [ ] **M8.1 — Verify zero-clone `uvx` install and lead with it**
  - **Scope:** Verify `uvx --from people-context people-context-mcp --help` plus one real stdio round trip from a
    clean environment; reorder README Quick start ahead of tool-install and source-checkout paths.
  - **Touches:** `README.md`; optional releasing cross-reference.
  - **Acceptance:** primary distribution name is `people-context`; commands and evidence are recorded in the PR;
    no behavior/package change.
  - **Out:** Registry, MCPB, editor configs, Docker.

- [ ] **M8.2 — MCP Registry and community-directory metadata**
  - **Scope:** Add root `server.json`, packaged `mcp-name:` marker, pinned Registry validation, and a current
    Smithery/PulseMCP/mcp.so/Glama submission matrix plus any required static in-repo metadata files.
  - **Acceptance:** top-level server version equals the `people-context` PyPI package-entry version; stdio package
    transport is schema-valid; namespace decision recorded; each directory's primary docs are cited and its
    manual-vs-repository submission path is explicit; required validators are pinned.
  - **Out:** live publication/approval and MCPB package entry.

- [ ] **M8.3 — Native-UV MCPB bundle and editor configs**
  - **Scope:** Add `mcpb/manifest.json`, root bundle `pyproject.toml`, `server/main.py`, exact reviewed MCPB build
    tooling, release artifact attachment, and Cursor/Windsurf/VS Code `uvx` snippets.
  - **Acceptance:** native `server.type="uv"`; semantic `manifest.json.version` and dependency pin match the release;
    schema `manifest_version` validated separately; archive contents inspected; clean-machine Desktop smoke test.
  - **Security:** document local Python/filesystem permissions; do not vendor an interpreter or model MCPB as an
    arbitrary shell command.
  - **Out:** Docker and stable MCPB Registry URL/digest entry.

- [ ] **M8.4 — Optional non-root Docker image and GHCR release**
  - **Scope:** Multi-stage Dockerfile, `.dockerignore`, tag-triggered GHCR workflow, README volume/env usage.
  - **Acceptance:** pinned base-image digest and Actions; non-root runtime; stdio default; explicit host-mounted DB;
    runtime makes no surprise network calls; image smoke test includes one stdio round trip.
  - **Out:** HTTP-default image.

## M9 — Cold start & onboarding

- [ ] **M9.1 — Relocate import router without dropping `mbox`**
  - **Scope:** Move explicit dispatch to `adapters/import_router.py`.
  - **Acceptance:** `email`/`mbox` → email extractor, `vcard` → vCard extractor, unknown →
    `invalid_source_type`; preserve `mbox` path-only semantics and E2E coverage.
  - **Out:** ICS and LinkedIn implementations.

- [ ] **M9.2 — ICS attendee import**
  - **Scope:** Add `IcsImportExtractor`, router branch, adapter/app/MCP tests.
  - **Acceptance:** cross-event dedup by normalized email; alternate names accumulate; one neutral calendar
    interaction per valid event; self addresses excluded; event title/description sentinels never stage.
  - **Out:** LinkedIn and onboarding commands.

- [ ] **M9.3 — LinkedIn connections CSV import**
  - **Scope:** Add extractor and router branch for person, optional affiliation, and optional connected-date fact.
  - **Acceptance:** preserve four earlier source values; tolerate documented header supersets; per-row failures are
    independent; profile URL/free text never persist; stable batch refs avoid duplicate-ref failures.
  - **Out:** onboarding commands.

- [ ] **M9.4 — Safe `init` and packaged `demo`**
  - **Scope:** Compose existing use cases for interactive onboarding; add deterministic dedicated demo database.
  - **Acceptance:** create self **before** vCard import with supplied email handles as `AliasKind.HANDLE`; own card
    with a seeded handle and its dependants are not staged; no-handle same-name card can only match the existing
    self, never create a duplicate. Demo data ships in the wheel, never `tests/fixtures`; ignores real DB settings;
    prints path-targeted server launch and graph-tool examples.
  - **Out:** MCP onboarding and external live integrations.

## M10 — Agent utilization

- [ ] **M10.1 — Package usage and end-of-session capture skill**
  - **Scope:** Add root `skills/people-context-usage/SKILL.md` covering resolution-first behavior, context vs
    communication guidance, strict candidate vocabulary, elevation gates, and review-only capture proposal.
  - **Acceptance:** never commits an extracted batch automatically; plugin validation and scripted transcript pass.
  - **Out:** user-invocable entry points and server instructions.

- [ ] **M10.2 — Add who/remember/reminders entry-point workflows**
  - **Scope:** Three thin user workflows over existing tools; they are not incorrectly constrained to one call.
  - **Acceptance:** `who` resolves then reads only when unambiguous; `remember` distinguishes explicit assertion
    from extracted context and preserves review; `reminders` resolves optional person filter; no elevated tools.
  - **Out:** automatic assertion/extraction heuristics.

- [ ] **M10.3 — Name under-used tools in server instructions**
  - **Scope:** Minimal `SERVER_INSTRUCTIONS` string addition for guidance and staging.
  - **Acceptance:** no signature/annotation/response change; no encouragement of elevated tools; literal tests update.
  - **Out:** tool-annotation changes.

## M11 — Sync bundle & bootstrap restore

- [ ] **M11.1 — Unbounded changelog read**
  - **Scope:** Widen `list_entries(limit: int | None = 100)`; `None` returns all rows.
  - **Acceptance:** existing default and descending `sync-log` behavior unchanged; unbounded deterministic coverage.
  - **Out:** bundle models/export.

- [ ] **M11.2 — Strict bundle export and private-file primitive**
  - **Scope:** Add strict v1 bundle DTOs, single-snapshot `BundleReader`, `ExportSyncBundle(BundleReader, Clock)`,
    `sync push`, and `atomic_write_private_text`; migrate existing JSON export to the helper.
  - **Acceptance:** literal format/version, nested `extra="forbid"`, no restore-input defaults, stable ordering,
    byte-identical output for same snapshot/clock, unbounded changelog, owner-private atomic replacement.
  - **File tests:** overwrite pre-existing `0644` → `0600` on POSIX; replace symlink entry without modifying its
    target; failed write preserves prior valid destination.
  - **Out:** restore.

- [ ] **M11.3 — Fail-closed bootstrap restore**
  - **Scope:** `BootstrapRestorer`, `RestoreSyncBundle`, `sync pull` with preview/confirmation.
  - **Pre-write validation:** wrong format/version, missing/unknown/malformed nested fields, duplicate ids, missing or
    retired origin, dangling references, watermark below any changelog/device HLC all fail before preview/prompt.
  - **Baseline target:** under `BEGIN IMMEDIATE`, require exactly one active local device, only canonical seeded
    vocabulary, and zero rows in every mutable domain, audit, sync, staging, FTS, and optional vector table — not
    merely zero people/changelog. Report counts/categories without deleting or exposing record contents.
  - **Transaction:** reject bundled/local device-id collision; reconcile incoming vocabulary; retire imported
    devices; insert domain/audit/changelog verbatim; rebuild FTS; advance local HLC; commit. Any failure rolls back.
  - **Acceptance:** no new audit/changelog rows; structured invalid/non-baseline refusals; per-table baseline,
    concurrency, and every-phase rollback tests.
  - **Out:** incremental replay/conflicts/encryption.

- [ ] **M11.4 — Multi-device E2E sign-off**
  - **Scope:** A→B stdio/CLI round trip plus B→C historical-device chain.
  - **Acceptance:** portable content and custom vocabulary parity; later B write sorts after imported history and
    uses B's id; all imported devices remain retired and are carried forward.
  - **Out:** protocol expansion.

## M12 — Trust, stability & v1.0

- [ ] **M12.1 — Publish compatibility promise**
  - **Scope:** Add and link `docs/compatibility.md`.
  - **Acceptance:** additive MCP and stable-JSON fields, forward-only DB migrations, compatible CLI defaults;
    deterministic vault Markdown explicitly not frozen; no invented deprecation window.
  - **Out:** release bump, encryption, threat comparison.

- [ ] **M12.2 — Synchronize 1.0 server release metadata and lock**
  - **Scope:** Update `pyproject.toml`, `server.json`, MCPB manifest/project, `uv.lock`, classifier, and release docs.
  - **Acceptance:** five semantic server-release fields equal `1.0.0`; MCPB schema version independent; Registry
    entry located by identifier; `uv.lock` root package reflects 1.0; `uv lock --check`.
  - **Version domains:** compatibility shim and Claude/Codex/OpenClaw/Obsidian plugin versions remain independent;
    only internally synchronize/bump an integration manifest when that artifact is intentionally published.
  - **Out:** tag/release and SQLCipher.

- [ ] **M12.3 — Dated threat comparison and README demo**
  - **Scope:** Primary-source, “as of” local-vs-cloud comparison; README packaged-demo walkthrough.
  - **Acceptance:** compare storage, breach/legal exposure, offline operation, deletion; factual language and valid
    links/assets.
  - **Out:** product telemetry or demo behavior changes.

- [ ] **M12.4 — Opt-in SQLCipher path with locked dependency state**
  - **Scope:** encrypted extra, `open_encrypted_db`, server/global CLI flag, `uv.lock`, tests/docs.
  - **Acceptance:** key applied before schema access/migrations; key only from non-empty env var; no plaintext
    fallback or key leakage; correct/wrong/plain-reader/WAL sentinel tests; supported-platform wheel probe.
  - **CI:** `uv lock --check`, `uv sync --locked --all-extras`, locked Ruff/tests; encrypted extra actually installed.
  - **Out:** default encryption, rotation, keychain, multi-key.

## M13 — Daily utility

- [ ] **M13.1 — Stale relationships MCP/CLI report**
  - **Scope:** `RecencyReader`, SQLite query, app policy, read-only MCP tool, CLI.
  - **Acceptance:** one row/person; all active categories; only public/personal interaction timing/count; zero
    ordinary interactions sort first; cap/truncation and SQL-level sensitivity tests.
  - **Out:** health scores/elevated variant.

- [ ] **M13.2 — Upcoming dates MCP/CLI report**
  - **Scope:** `ListUpcomingDates(PersonContextReader, ListReminders, PersonReader, Clock)`.
  - **Acceptance:** `window_days` validated in `0..366`; inclusive window; annual projection of `YYYY-MM-DD` and
    `--MM-DD`; actual leap days only; stored reminder calendar-date component; missing/deleted people skipped;
    elevated facts invisible even to skip counts.
  - **Out:** additional predicates/elevated variant.

- [ ] **M13.3 — Meeting-prep skill and private reminder ICS export**
  - **Scope:** extend M10 skill; deterministic `reminders-ics` CLI using M11 private writer.
  - **Acceptance:** export only reminders with aware `due_at` and `created_at`; canonical UTC/folding/escaping;
    `skipped_undated` and `skipped_naive_datetime` omit invalid rows without guessing a timezone; supported exact
    RRULE vocabulary; `recurrence_omitted` counts exported reminders whose unsupported RRULE was omitted;
    byte-identical output; overwrite/symlink/failure safety inherited and tested.
  - **Out:** write-contract timezone enforcement, VEVENT, and third-party push.

- [ ] **M13.4 — Deterministic local changelog watch**
  - **Scope:** additive ascending `list_entries_after`; JSONL polling CLI.
  - **Acceptance:** without `--from-start` begin at current latest without replay; with it replay all; full cursor
    key and stable multi-batch advancement; testable one-poll/sleep seam; stdout only, no network/state.
  - **Out:** daemon/network sink; optional future `--once`.

## M14 — Ecosystem interoperability

- [ ] **M14.1 — Stable person brief and person-index JSON**
  - **Scope:** `ComposePersonBrief`, Markdown/JSON CLI, `list --json`, private file output.
  - **Acceptance:** versioned additive JSON; all open reminder kinds; sensitive flag widens context only while
    guidance remains ordinary; explicit disclosure labels; deterministic ordering and secure overwrite tests.
  - **Out:** new MCP tool or guidance-contract change.

- [ ] **M14.2 — Deterministic vCard writer**
  - **Scope:** typed vCard port/DTOs, app projection, filesystem adapter, CLI using private writer.
  - **Acceptance:** `FN` canonical; `N` stores the whole canonical name in one component without guessing name
    parts; active/as-of/sensitivity filtering; one deterministic affiliation; one full `YYYY-MM-DD` birthday;
    separate omitted-valid, skipped-partial, and skipped-unparseable counts; 3.0/4.0 canonical bytes and unchanged-
    importer exact round trip.
  - **Out:** CardDAV, multi-affiliation/birthday encoding, partial-birthday importer normalization.

- [ ] **M14.3 — Outlook and WhatsApp extractors**
  - **Scope:** new extractors; explicitly widen Protocol/router/all implementations with `self_names` and
    `self_sender`.
  - **Acceptance:** preserve five prior sources; WhatsApp body never enters any output/log/error; self sender creates
    no person and is omitted from participant refs; self participation is implicit; self-only day creates no
    interaction; seven-source router/E2E tests.
  - **Out:** Signal and candidate-schema self field.

- [ ] **M14.4 — Safe read-only Obsidian plugin and distribution mirror**
  - **Scope:** package, `package-lock.json`, CLI bridge, panes, tests, deterministic mirror/release workflow.
  - **Execution:** stable person id only; `spawn`/`execFile` argument arrays, `shell:false`, no command string/freeform
    args; timeout, cancellation, bounded stdout/stderr, inert metacharacter fixtures.
  - **Settings:** executable, optional DB path, encrypted boolean, refresh policy; fixed arg array; encrypted mode
    inherits but never stores/logs `PEOPLE_CONTEXT_DB_KEY`; missing key fails without plaintext fallback.
  - **Build:** `npm ci --no-audit --no-fund`; clean lockfile builds; compare artifact checksums; desktop-only manifest.
  - **Out:** DB writes/raw SQLite/community submission itself.

## M15 — Data quality & credibility

- [ ] **M15.1 — Deterministic doctor findings**
  - **Scope:** `CurationReader`, SQLite queries, app report policy, CLI; optional next-free index.
  - **Acceptance:** stable codes for duplicate alias/handle, contradictory fact, soft-deleted references; handle
    precedence; fact overlap exactly matches `ValidityPeriod.overlaps`; report-only and exit zero with findings.
  - **Actions:** JSON suggestions are structured `{surface, argv}` CLI actions or `{surface, tool, arguments}` MCP
    actions using ids, never shell-interpolated names; doctor JSON is versioned/stable additive.
  - **Out:** interactive repair and MCP findings tool.

- [ ] **M15.2 — Aggregate-only stats report**
  - **Scope:** narrow `StatsReader`, SQLite aggregate adapter, app redaction, CLI.
  - **Acceptance:** no record text/device names/paths from adapter; CLI passes environment-gate booleans and optional
    path; path redacted by default; versioned stable aggregate JSON; no network/server probe.
  - **Storage:** report main + existing WAL/SHM bytes; in-memory/unavailable path is explicit null/storage-kind,
    never misleading zero.
  - **Out:** doctor findings/telemetry.

- [ ] **M15.3 — Additive transliteration match detail**
  - **Scope:** optional descriptive `match_detail`, bilingual fixtures/docs.
  - **Acceptance:** preserve `match_reason="exact"`, score/ranking/ambiguity; canonical match wins then deterministic
    alias-kind detail; CJK and non-CJK bidirectional fixtures.
  - **Out:** fuzzy cross-script matching or ranking change.

- [ ] **M15.4 — Reproducible eval harness and use-case gallery**
  - **Scope:** fictional fixture DB, fixed tasks/rubrics, with/without MCP runs, dated docs, narrative recipes.
  - **Acceptance:** prompts/model ids/harness version recorded; keys only in environment; no real personal DB;
    network-free stub dry run validates scoring; production package excludes eval assets.
  - **Out:** hosted telemetry benchmark.
