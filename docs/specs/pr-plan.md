# M8–M20 pull-request plan

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
| M20 | Streaming importer parsing | 3 |
| **Total** | | **42** |

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
- M18.1 → M18.2: durable source sessions and candidate→record mappings exist before source inspection consumes
  them.
- M18.1/M18.3 → M19.1/M19.2: timeline and consolidation can explain source provenance and evidence when present.
- M16.1 → M20.1: the streaming reader and parser-work budget extend the `ImportBudget` seam M16 introduced,
  and must not change the ceilings or the values it already carries.
- M20.1 → M20.2/M20.3: the shared streaming reader and budget exist before the mbox and WhatsApp conversions
  consume them.

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

- [x] **M12.2 — Synchronize 1.0 server metadata and lock**
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

- [x] **M16.1 — Expose the existing import lifecycle through `pctx`**
  - **Scope:** Add `pctx import stage SOURCE PATH`, `import review`, and `import commit --all|--accept`, all with
    stable `--json`; support exactly the seven existing router sources; refactor shared vCard onboarding rendering /
    selection only enough to avoid a second CLI implementation; bound file staging **and existing-batch review/commit
    reads** at the new CLI process boundary.
  - **Acceptance:** stage never commits; `--all` and repeatable canonical `--accept` are mutually exclusive and one
    is required; no second confirmation prompt; stable v1 batch/review/commit JSON documents are deterministic.
    `pctx import stage` rejects source files over **64 MiB**, more than **100,000 staged candidates**, or more than
    **64 MiB persisted reviewable staging payload** (at minimum UTF-8 bytes of staged `source` + candidate JSON),
    stopping before unbounded read/accumulation and before durable staging; path-only `mbox` obeys the byte budget
    while processing. Before either `import review` or commit form calls any full-batch `list_batch` path, a narrow
    SQLite preflight computes row count plus persisted reviewable payload bytes without loading candidate JSON and
    rejects batches over **100,000 rows or 64 MiB** with a safe `batch_too_large_for_cli`-style error and no mutation.
    This makes M16-created batches self-reviewable while safely refusing oversized legacy MCP batches at the new CLI
    boundary only; released MCP `import_content`/`review_import`/`commit_import` and `pctx init` behavior is not
    retroactively narrowed. Resource errors never echo payload text; installed CLI stage→review→commit→list E2E
    passes; existing `pctx init` semantics remain unchanged.
  - **Out:** new importers/candidate types, stdin/raw-content import, `stage_candidates` CLI, batch management,
    embedded models/network, general CLI/MCP parity, retroactive global import-size/read caps.

## M17 — Agent-assisted knowledge extraction

- [x] **M17.1 — Expand and bound strict candidates to observation, trait, and relationship**
  - **Scope:** Add strict candidate models/staging dependency rewriting and `CommitImport` support through existing
    `RecordObservation`, `RecordTrait`, and `SetRelationship` use cases; bound the new MCP extraction forms in this
    same PR.
  - **Acceptance:** staged traits require explicit confidence and non-blank concise evidence note; relationship
    candidates use batch-local person refs and exactly preserve `SetRelationship` semantics—known vocabulary/
    synonyms canonicalize, normalized syntactically valid unknown types remain legal uncategorized edges, and only
    blank/non-word values fail. Relationship candidates are **ordinary-disclosure only** because the current durable
    `Relationship`/graph read contract has no enforceable sensitivity field: do not add a candidate-only sensitivity
    value that is discarded at commit, and strict extra fields must fail rather than imply protection. New M17
    candidate fields carry the binding string limits in the milestone spec. **Any MCP request containing at least one
    M17 candidate** is capped at 500 total candidates, a normalized 128-character `source`, **1 MiB canonical UTF-8
    serialization of the complete candidate array, and 8 KiB for every string field on every candidate—including
    legacy person/fact/interaction/affiliation fields in a mixed request**. The tighter M17 field limits also apply.
    Rejection occurs before staging and never echoes payload values. For the same M17-containing request, person
    matching must preserve explicit **unmatched / matched / ambiguous** disposition across canonical-name + handle
    identity tokens: zero distinct active matches is unmatched, exactly one is matched, and more than one is
    ambiguous. An ambiguous person exposes bounded reviewable match state, has no authoritative
    `matched_person_id`, **must not call `RememberPerson` as a new identity when accepted**, remains unresolved, and
    leaves every accepted dependant unresolved until deterministic re-evaluation yields one active person or the
    candidate is corrected/re-staged. A unique token must not short-circuit conflicting matches on another token.
    A genuinely legacy-only MCP batch retains its released accepted shape **and pre-M17 matching behavior**. People
    commit/resolve before dependants; unresolved refs remain unresolved; all durable writes retain normal
    audit/changelog/provenance; existing candidate envelopes remain additive-compatible.
  - **Out:** source receipts/evidence tables, raw-text parsing, LLM/runtime dependency, automatic commit/confidence,
    elevated relationship storage/disclosure, retroactive global caps or ambiguity-semantics changes on genuinely
    legacy-only MCP staging.

- [x] **M17.2 — Add bounded agent candidate CLI and unstructured-source workflow**
  - **Scope:** Add `pctx import stage-candidates --source SOURCE --input PATH|- [--json]` and extend the packaged
    agent skill for transcript/note extraction.
  - **Acceptance:** input is candidate JSON, never raw transcript; reject >1 MiB input, >500 candidates, >8 KiB
    generic CLI strings, >128-character source labels, and the tighter M17 field limits before staging;
    malformed/extra fields fail closed and rejected payloads are never echoed. The MCP mixed/new-request count,
    source, payload, all-string, new-field, and ambiguity-preserving identity rules already shipped in M17.1 remain
    unchanged. Workflow distinguishes explicit fact vs observation vs inferred trait, uses candidate matching for
    identities, discourages speculative sensitive inference/verbatim evidence, and **omits sensitive/restricted
    relationship edges even when explicitly stated** rather than downgrading them to the ordinary graph. It stages
    only and requires explicit later commit.
  - **Out:** model invocation, prompt storage, transcript persistence, automatic review/commit, source idempotency,
    relationship-sensitivity schema/read changes, retroactive global size/count/source narrowing of legacy-only MCP
    staging.

## M18 — Provenance, idempotency & evidence

- [x] **M18.1 — Add durable source claims, commit mappings, and bootstrap v2**
  - **Scope:** Add the next-free migration and ports/app/SQLite support for source sessions plus durable candidate
    commit-outcome mappings; ensure each file digest/extraction describes one verified stable snapshot; define
    extraction-configuration fingerprints; add explicit `pctx import stage ... --force`; advance strict full
    bootstrap export to v2 while retaining v1 restore support; integrate mapping state **and retained staged person
    matches** with commit transaction grouping, person merge, hard forget/retained staging/receipt scrubbing, and
    baseline-empty restore checks.
  - **Acceptance:** byte-capable importers hash and parse the same immutable bytes under the existing M16 resource
    budgets; path-only `mbox` verifies identity/metadata plus pre/post SHA-256 and discards/retries or fails safely if
    the source changes; no durable receipt/batch/candidate appears for a mismatched pass and no raw temporary copy is
    created. Structured duplicate identity is `(source_kind, content_digest, extraction_fingerprint)`, where the
    fingerprint deterministically covers extraction-affecting normalized self inputs/`self_sender` and a per-source
    extraction-contract revision without persisting raw self values. Agent sessions with a digest may participate in
    a claim; if their optional fingerprint is absent, uniqueness uses an explicit internal absence state rather than
    SQLite NULL-distinct behavior. A digestless agent session has **no canonical duplicate claim** and makes no
    source-level idempotency promise. `source_kind` is a bounded non-personal machine category, while optional human
    label/external source id are separately bounded caller metadata. Claim + source session + batch/candidate
    publication are atomic under a uniqueness mechanism; `--force` creates a distinct non-default processing session
    for an identical claim. If a default duplicate claim resolves to a terminal claim-backed `redacted` source with
    no batch association, staging **refuses rather than fabricating/reusing a batch**: stable application code
    `source_previously_redacted`, non-zero CLI exit, empty stdout even under `--json`, bounded safe stderr with
    `--force` guidance, and no mutation. A forced retry may create the ordinary distinct batch/success document.

    Every successful `CommitImport.execute` mints/propagates **one non-empty logical `transaction_id`** across every
    child entity mutation, candidate mapping mutation, and source-session status mutation produced by that commit;
    multiple accepted candidates share it, unresolved rows emit no durable effect, and any phase failure rolls the
    whole commit back. Every committed candidate records a durable mapping/outcome with its mutation/status;
    matched/reused entities are legal and committed retries use that mapping rather than heuristics.

    Person merge updates mappings inside the existing merge transaction and **same merge transaction id**: duplicate
    person mappings retarget to the survivor; reparented records keep surviving ids; deduped relationship mappings
    retarget to the chosen keeper; a relationship removed as a merge-created self-loop becomes terminal
    `merged_away` with no entity id and never recreates the edge on retry. **Every retained person staging row whose
    `matched_person_id` is the duplicate is retargeted to the survivor in the same SQLite merge transaction**, so a
    later dependent commit cannot resolve through an inactive duplicate. Operational staging retargeting mints no
    audit/changelog row but rolls back atomically with the merge. Exported incomplete staging carries the survivor id;
    source inspection/bootstrap understand terminal mappings without dangling ids.

    Record/person hard forget previews and deletes mappings to entities actually erased, redacts their mapping audit
    and covered changelog histories/replay state, and keeps mappings to shared interactions that remain durable.
    In the **same forget transaction**, retained staging rows structurally linked through typed candidate/person/
    endpoint/participant/evidence ids are deleted recursively to a fixed point (including pending `matched_person_id`
    and explicit durable `evidence_ids`); no free-text/name guessing. Preview/result counts include removed staging
    rows. **Whenever that forget removes any mapping or staging row belonging to an M18 source, scrub that source's
    opaque caller-authored human label, external source id, and other optional inspection metadata plus their
    audit/covered-changelog history even if unrelated mappings/reviewable rows survive.** Keep internal claim fields,
    status, and any batch association still required for surviving review state. If the source is then left with no
    live mapping and no reviewable staging, lifecycle depends on claim availability: a **claim-backed** session
    becomes terminal `redacted`, additionally clears its batch/remaining optional inspection state, and retains only
    internal id + canonical claim key + status; a **digestless** session is deleted entirely and its receipt history
    redacted because `(source_kind,null,null)` is not a usable duplicate claim. Redacted inspection exposes no former
    timestamps/batch/counts/mappings; claim-backed redaction remains non-restageable except through explicit
    `--force`, while a later digestless source may stage anew.

    Export emits strict sync-bundle v2; restore accepts v1/v2. V2 carries **all surviving source sessions and all
    candidate commit mappings/outcomes, including fully committed sessions after staging cleanup**; staging rows are
    carried only for staged/partially committed batches and any retained `matched_person_id` must reference the
    active post-merge survivor. Live-entity mappings must reference bundled durable entities; terminal `merged_away`
    mappings have no entity id and terminal `redacted` source rows must be claim-backed and satisfy the minimal-claim
    invariant; fully forgotten digestless sessions are absent. M18.1 source-session/mapping tables are added to the
    existing transactional baseline-empty check for **both v1 and v2 restore**, so an older incoming document cannot
    merge into local M18 state. Unknown fields still fail per declared version.
  - **Out:** semantic candidate dedup, trait evidence, source rollback, folder watch, incremental peer replication of
    staging state, raw extraction-option persistence, heuristic free-text staging erasure.

- [x] **M18.2 — Expose bounded source provenance and inspection over commit mappings**
  - **Scope:** Add local `sources` / `source show` inspection and any narrow app/read ports needed to traverse M18.1
    candidate→entity→source-session mappings while preserving existing `Provenance.session` meanings.
  - **Acceptance:** existing message/event-id `Provenance.session` semantics remain byte/semantically unchanged.
    `pctx sources` uses default `limit=50`, accepted range `1..200`, deterministic `(created_at DESC, id DESC)` order,
    and an opaque validated keyset cursor. The SQLite read itself applies the cursor predicate plus `LIMIT limit + 1`
    and returns at most one bounded page with nullable `next_cursor`; rendering must never materialize the complete
    source table first. **`pctx source show SOURCE_SESSION_ID [--limit N] [--cursor CURSOR]` independently pages the
    source's candidate mappings with default 50/max 200, deterministic `candidate_id ASC` keyset order, validated
    opaque cursor, and SQLite `LIMIT limit + 1`; aggregate candidate/status counts are computed in SQL rather than by
    loading all mappings.** Non-redacted inspection returns metadata plus at most that bounded mapping page and
    nullable mapping `next_cursor`; if an earlier hard forget affected that source, its caller-authored label/external
    id/optional inspection metadata remain absent even while survivor mappings are shown. Terminal merge outcomes
    contain no removed edge id. A terminal redacted source is always claim-backed and returns only internal id +
    non-personal source kind + digest/fingerprint-or-absence claim state + status, never its cleared label/external
    id/batch/timestamps/counts/mappings; a fully forgotten digestless source has no retained row. No raw
    source/path/self-configuration leak; mappings remain usable after staging cleanup policy; completed-source
    mappings survive bootstrap restore, while hard-forgotten mappings/staging/caller metadata are absent;
    partial/idempotent commits are understandable. This PR does not change the strict v2 bootstrap shape introduced
    by M18.1.
  - **Out:** trait evidence links, source rollback/delete cascade, document retrieval, confidence recomputation,
    second parallel record-source provenance table, offset/unbounded source or mapping scans.

- [x] **M18.3 — Ground traits in durable evidence records and bootstrap v3**
  - **Scope:** Add the next-free additive trait-evidence relation plus a bounded caller-addressable same-batch
    evidence-ref rewrite; support evidence committed in an earlier partial commit and explicit durable evidence ids;
    advance strict bootstrap export to v3 while retaining v1/v2 restore support; integrate the new relation with
    hard-forget lifecycle/redaction and the baseline-empty restore contract.
  - **Acceptance:** observation/interaction candidates may add optional unique non-blank `evidence_ref` tokens of at
    most **256 characters**. Traits may reference up to **32 combined** unique same-batch `evidence_refs` and durable
    `evidence_ids`; each `evidence_id` is a **format-opaque non-blank identifier of at most 256 characters** and is
    preserved exactly—do not require/canonicalize ULID shape, so valid restored/custom ids such as `obs-1` remain
    addressable. Blank/overlong ids and unknown/duplicate/wrong-type refs fail before staging without echo. Staging
    rewrites caller refs to canonical `evidence_candidate_ids` exactly like person-ref rewriting, so callers never
    need preallocated candidate ids or an append-to-batch API. Rewritten candidate ids resolve through the M18.1
    live-entity mapping whether evidence was committed in a prior invocation or earlier in the current one; explicit
    durable ids use exact lookup semantics; only active supported evidence types are legal. An observation must belong
    to the trait subject and an interaction must include that subject; stable id ordering; accepted trait remains
    unresolved when required evidence has no valid live mapping/cannot resolve/does not exist/resolves to another
    person's evidence.

    Hard forget of a trait/evidence entity deletes affected evidence relations in the same transaction, includes
    them in preview/replay affected state, redacts their audit/covered changelog history, and preserves links to
    shared interaction evidence only when that interaction itself remains durable. M18.3 adds the trait-evidence
    table to the transactional baseline-empty check for **v1, v2, and v3 restore**. Persisted-id (including non-ULID
    restored ids), earlier-partial-commit, same-invocation, ref-bound, record/person-forget, and baseline-version tests
    are required; retrieval respects sensitivity and never exposes restricted evidence through a visible trait;
    `evidence_note` remains additive human context. Existing interactions without `evidence_ref` remain unchanged.
    Export emits strict sync-bundle v3 with trait-evidence relations **and inherits v2's all-commit-mapping/outcome
    and claim-backed terminal-redacted-source rules**; restore accepts v1/v2/v3 and validates each version fail-closed.
  - **Out:** trait→trait evidence, automatic confidence formula, automatic evidence deletion/correction propagation,
    append-to-batch mutation or caller control of canonical candidate ids.

## M19 — Knowledge consolidation & temporal views

- [ ] **M19.1 — Add bounded person timeline reads**
  - **Scope:** Add a narrow timeline port/use case, bounded SQLite projection, local `pctx timeline` and ordinary-
    disclosure MCP read over interactions/observations/dated state/traits plus M18 provenance/evidence where useful.
  - **Acceptance:** deterministic effective-time ordering with stable tie-breaks; explicit undated behavior; app and
    underlying traversal/query work bounded; ordinary MCP excludes restricted data; CLI sensitivity opt-in is
    explicit; timeline is read-only projection, not a denormalized event store; stable JSON if documented.
  - **Out:** audit-log dump, new timeline table, automatic history rewriting.

- [ ] **M19.2 — Add consolidation context, atomic fact supersession, and review-only maintenance workflow**
  - **Scope:** Provide a person-scoped read model exposing duplicate/superseding/reinforcing/contradictory knowledge;
    add narrow atomic `SupersedeFact` + `supersede_fact` MCP mutation; extend the agent skill to propose structured
    approved maintenance actions.
  - **Acceptance:** deterministic bounds/order; M15 doctor remains unchanged; multiple observations can remain
    distinct supporting evidence; `correct_record` remains for erroneous data and never overwrites an historically
    correct fact merely because a newer value is true. `supersede_fact` preserves old person/predicate/value/
    provenance, requires `effective_from` to remain inside any bounded old validity period, closes old inclusive
    validity at `effective_from - 1 day`, and creates the new same-person/predicate fact at `effective_from` with the
    old fact's **original `valid_to`** (open-ended stays open-ended; bounded stays bounded). It audits/changelogs both
    rows atomically with rollback on either phase failure. Both row-level changelog effects share one non-empty
    logical `transaction_id` passed through `audit_mutation`, and tests assert that grouping explicitly. Tests also
    pin bounded-period endpoint inheritance including `effective_from == old.valid_to`. Agent explains proposals and
    waits for explicit approval before supersession/correction/merge/other mutations; no report/read path writes
    audit/changelog/domain state; scripted approval/correction-vs-supersession regression passes.
  - **Out:** autonomous belief updater, confidence-by-count formula, background maintenance daemon, required semantic
    vector clustering, generic consolidation/batch mutation, automatic merge/correction, independent replacement
    `valid_to` editing, supersession for every record type.

## M20 — Streaming importer parsing

- [ ] **M20.1 — Add the streaming source reader and parser-work budget**
  - **Scope:** Add a bounded line/record-oriented streaming counterpart to `read_source_text` and a narrow
    parser-work budget bounding **live retained parsed records**, both defaulting to unbounded exactly as
    `max_source_bytes` and `max_candidates` do; convert the sources with no cross-file state — `vcard`, `ics`,
    `linkedin`, `outlook` — to consume them.
  - **Acceptance:** the streaming reader reproduces `read_source_text` decoding byte for byte, including
    universal-newline handling, `utf-8-sig` BOM stripping, and an `undecodable_source` refusal raised for the
    same inputs at the same point regardless of chunk boundaries. Lazy unfold/split for vCard and iCalendar and
    streamed CSV input for LinkedIn and Outlook produce **byte-identical** staged candidates, ordering, refs,
    skip reasons, and one-based indexes to the current implementation, proven by a table-driven equivalence
    corpus. `linkedin`'s whole-file `splitlines` is removed and its canonical-header preamble scan stays
    bounded. A candidate-free source retains records bounded by the budget rather than by input size, asserted
    against the budget seam. The M16 ceilings and their values are unchanged and no new user-visible limit is
    added; an unbudgeted caller is byte-for-byte unaffected.
  - **Out:** `mbox` and `email` conversion, WhatsApp, MCP-path work, new sources/candidate types, any change to
    the M16 ceilings or to which candidates a source yields.

- [ ] **M20.2 — Stream mbox messages and meter email address expansion**
  - **Scope:** Stop materializing `list(mbox)`; move mailbox-handle ownership into the extractor's scope so the
    mailbox is consumed lazily by the extraction loop, and pass the budget into `_correspondents` so one
    message's address expansion is metered while it is built rather than after `getaddresses` returns.
  - **Acceptance:** the mailbox stays open for the whole iteration and is closed exactly once on every path,
    including extraction failure; `MeteredSourceFile` continues to meter every byte `mailbox` reads, so the
    existing M16 scan-metering, growth, per-read cap, and exact-ceiling boundary tests pass unchanged. An mbox
    of messages with no external correspondents completes with live retained records bounded rather than
    proportional to message count. Staged candidates, ordering, refs, `skipped_message_ids`, and
    `skipped_without_id` are byte-identical to the current implementation for the equivalence corpus.
  - **Out:** WhatsApp, MCP-path work, changing `mbox`'s path-only contract or the 64 MiB source budget.

- [ ] **M20.3 — Bound WhatsApp resolution and extend the bound to `import_content`**
  - **Scope:** Replace WhatsApp's retain-every-`_Message` resolution with a bounded one, and extend the
    milestone's bound to the released MCP `import_content` path.
  - **Acceptance:** the chosen WhatsApp resolution is stated in the pull request with its reasoning. Preferred
    is a **bounded two-pass scan** whose first pass retains only O(1) ordering evidence and whose second pass
    re-applies the byte budget and refuses rather than mixing two versions if the source changed between passes;
    the existing M14 ordering-inference and skip-reason tests then pass unchanged. If instead the whole-file
    inference is narrowed to a documented bounded prefix, that is an explicit M14 behavior change and
    `docs/import.md` plus the M14 spec are updated in this same pull request — never a silent consequence.
    Candidate-free malformed input stays bounded. **The MCP bound comes from streaming, never from rejection:**
    `import_content` accepts exactly the sources it accepts today, with identical staged candidates and
    identical errors, proven by regression tests; no parameter is added, narrowed, or re-defaulted, so nothing
    here is a compatibility event.
  - **Out:** new rejection thresholds on any released MCP input, changes to the `pctx import` ceilings, new
    sources or candidate types, and any change to what a source extracts beyond the explicitly renegotiated and
    documented WhatsApp option.
