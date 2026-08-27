# Roadmap

Milestones are additive and preserve the hexagonal dependency rule.

## M0 — Foundation

Delivered the domain/schema scaffold, SQLite repository, stdio MCP server, initial identity tools, CLI, and
vertical-slice tests.

**Status:** Delivered.

## M1 — Identity and retrieval

Delivered the five-stage explainable resolution pipeline and bounded sensitivity-aware person context.

**Status:** Delivered.

## M2 — Full write surface, curation, and communication guidance

Delivered all record writes, corrections, reminders, communication philosophy/guidance, consistent provenance,
and polished audit behavior.

**Status:** Delivered.

## M3 — Lifecycle and import

Delivered merge, forget, JSON export, reviewable email/mbox import, and CLI curation commands.

**Status:** Delivered.

## M4 — Transport and retrieval upgrades

Delivered loopback Streamable HTTP, optional pinned multilingual semantic retrieval, vCard import, and strict
agent-extracted candidate staging.

**Status:** Delivered.

## M5 — Sync groundwork

Documented replication, dedicated changelog, conservative conflict handling, and future ownership/sharing
considerations without implementing sync runtime.

**Status:** Delivered as design only.

## M6 — Sync foundations

Added migration `002_sync_foundations.sql`, installation identity, persisted HLC, replayable changelog,
`sync_conflicts`, and one atomic unit-of-work seam spanning state, audit, clock, and changelog. Merge and forget
emit exact replay children/manifests or redacted ID-only tombstones. `sync-log` provides local inspection.

M6 deliberately added no exchange, pairing, relay, peer cursor, replay engine, bootstrap restore, or MCP sync
tool.

**Status:** Delivered.

## M7 — Relationship graph & vault export

**Goals:** make relationship semantics canonical and extensible, expose bounded structural graph traversal, and
provide a safe human-operated Obsidian export without changing existing response or sync contracts.

**Deliverables:**

- migration `003_relationship_vocabulary.sql` with seeded professional/family/social vocabulary and synonyms;
- write-time synonym resolution, inverse canonicalization, symmetric endpoint ordering, and active-edge update
  deduplication through the M6 atomic audit/changelog seam;
- additive perspective `display_type` in relationship hydration, context, guidance, and CLI show;
- add-only custom vocabulary curation plus dry-run/apply legacy relationship normalization;
- narrow `GraphReader` port with cycle-safe bounded breadth-first SQLite traversal;
- read-only `get_relationship_graph` and `find_connection` MCP tools with depth/node/edge caps and explicit
  truncation/not-found/not-connected contracts;
- CLI-only deterministic Obsidian vault export with marker-file ownership safety, Unicode/collision-safe names,
  organization hubs, perspective Dataview/wikilinks, durable facts/reminders, and explicit sensitivity opt-in;
- fake-port, real-SQLite, in-memory MCP, CLI, migration, and real-stdio E2E coverage.

**Status:** Delivered.

## M8 — Distribution & reach

**Goals:** cut the distance between “hears about this project” and “has it running in a client” to a single
command, without changing server behavior.

**Deliverables:**

- verified zero-clone install: `uvx --from people-context people-context` against the existing
  PyPI-published package;
- root `server.json` using the official MCP Registry package schema and packaged `mcp-name:` ownership marker,
  plus current metadata/submission coverage for Smithery, PulseMCP, mcp.so, and Glama;
- a native-UV Claude Desktop `.mcpb` bundle containing its root manifest/project and thin Python entry point —
  not a packaged `.claude-plugin/mcp.json` command wrapper;
- documented one-line stdio configs for Cursor, Windsurf, and VS Code alongside the existing generic/Claude Code
  instructions;
- an optional non-root local-stdio Docker image with a bind-mounted database volume and GHCR release workflow;
- pinned external validators/build CLIs, base-image digests, and Actions;
- README quick-start and docs-table updates.

No `domain`, `app`, `ports`, or MCP tool-surface changes are required; this milestone is packaging, metadata,
documentation, and CI only.

**Status:** Delivered.

## M9 — Cold start & onboarding

**Goals:** give a freshly installed, empty database something to show in under a minute, and broaden the
extract-and-stage import pipeline to the contact sources people actually export from.

**Deliverables:**

- `pctx init`: an interactive CLI onboarding command that first seeds the self person through
  `RememberPerson` with supplied email handles, then optionally runs the existing vCard
  `ImportContent` → `ReviewImport` → `CommitImport` flow, and prompts for an initial communication philosophy;
  the user's own contact card must not create a duplicate self record;
- `pctx demo`: seeds a small fictional dataset into a dedicated demo database (never the user's real
  `--db`/resolved path), ships its data in the installed wheel, and prints path-targeted server plus
  `resolve_person`, `get_relationship_graph`, and `find_connection` examples;
- two new import sources reusing the existing candidate vocabulary with zero schema/review-gate changes: `.ics`
  calendar attendees (`source_type="ics"`) and LinkedIn connections (`source_type="linkedin"`);
- `ImportExtractorRouter` lives in `adapters/importers/router.py` without dropping the existing `mbox` source;
- fake-port, real-SQLite, in-memory MCP, CLI, packaging, and stdio E2E coverage.

**Status:** Delivered.

## M10 — Agent utilization

**Goals:** make the existing tool surface easier for agents to use correctly and consistently. The milestone adds
prompt/plugin behavior and at most a minimal instruction string, not new business capabilities.

**Deliverables:**

- a packaged Claude Code skill describing resolution-first behavior, communication guidance, and the
  stage/review/commit import flow;
- user-invocable who/remember/reminders workflows (namespaced as `/people-context:who` etc.) composing existing
  tools;
- an end-of-session instruction asking the agent to propose staged candidates for durable facts — never an
  automatic `commit_import`;
- at most a small additive `SERVER_INSTRUCTIONS` extension naming `get_communication_guidance` and
  `stage_candidates`, without signature/annotation/response changes.

**Status:** Delivered.

## M11 — Sync bundle export and trusted bootstrap restore

**Goals:** give the M6 changelog foundations a first consumer beyond local inspection — a file-based bundle that
moves one device's complete state to a brand-new device, doubling as a backup. Incremental replay between
independently diverged devices remains deferred.

**Deliverables:**

- `pctx sync push --output DIR`: one strict versioned JSON bundle containing a single-transaction
  snapshot, relationship vocabulary, referenced devices, complete changelog, and HLC watermark;
- `pctx sync pull --input PATH`: strict format/version/nested validation before preview or writes, then
  empty-target-only atomic `BEGIN IMMEDIATE` restore of vocabulary, retired device history, primary/audit rows,
  and changelog, followed by FTS rebuild and local-HLC advancement;
- imported device identities are never active and a bundle/local device-id collision is rejected;
- an additive `Changelog.list_entries(limit=None)` read;
- a shared atomic owner-private file writer used by bundle/JSON and later personal-data exports;
- fake-port, strict-model, real-SQLite, concurrency, CLI, and stdio E2E coverage, including A→B→C continuity.

**Status:** Delivered.

## M12 — Trust, stability, and v1.0

**Goals:** formalize the compatibility discipline followed since M7 and close trust gaps around at-rest encryption
and cloud-memory comparisons.

**Deliverables:**

- a written additive MCP/stable-JSON, CLI-default, and forward-only DB compatibility promise;
- synchronized `1.0.0` primary server metadata (`pyproject.toml`, Registry package/server versions, MCPB semantic
  version/dependency pin) plus regenerated `uv.lock`, while integration plugin/shim versions remain explicit
  independent domains;
- opt-in SQLCipher behind a locked optional dependency extra and `PEOPLE_CONTEXT_DB_KEY`-gated connection path,
  leaving default `open_db` behavior unchanged;
- a dated, primary-source threat-model comparison with cloud memory tools;
- README demo polish based on the packaged M9 demo.

**Status:** Delivered. The synchronized `1.0.0` primary server metadata landed through the release automation
rather than a dedicated pull request: the `chore(main): release 1.0.0` release-please change set the root
project, Registry server version and `--from` pin, MCPB manifest version and dependency pin, and the `uv.lock`
root entry together, and `tests/test_packaging_metadata.py` pins all five to one another. The MCPB
`manifest_version` stays tooling metadata at `0.4`, and the integration plugin/shim versions remain the
independent domains described in [compatibility.md](compatibility.md#scope-and-versioning).

## M13 — Daily utility & proactive signals

**Goals:** give the store daily, explainable read-side utility over data already held; nothing new is recorded.

**Deliverables:**

- read-only `get_stale_relationships` MCP tool and `pctx stale` CLI, computed only over
  ordinary-disclosure interactions with one row per person and a categories list;
- read-only `upcoming_dates` MCP tool/CLI over ordinary birthday facts and active reminders, with annual
  month/day projection and real leap-day behavior;
- a meeting-preparation flow in the M10 skill;
- deterministic `pctx reminders-ics --output FILE` using the shared atomic private-file writer; dated
  reminders remain exported even when an unsupported recurrence rule is omitted and counted;
- `pctx watch`: local-only JSON-lines changelog tail with explicit initial-cursor and `--from-start`
  semantics.

**Status:** Delivered.

## M14 — Ecosystem & interoperability

**Goals:** meet adjacent tool ecosystems with portable briefs, existing-import-plus-one-way-vCard-export,
additional import funnels, and a first-class live Obsidian view.

**Deliverables:**

- `pctx brief PERSON [--include-sensitive]`: CLI-only deterministic Markdown/versioned JSON, explicitly
  distinguishing sensitive context from ordinary-only communication guidance;
- `pctx export-vcard`: deterministic unchanged-importer round-trip for non-heuristic names, one active
  affiliation, and one full-date birthday; partial/unparseable birthdays are counted rather than emitted
  non-standardly;
- Outlook contacts CSV and WhatsApp participant/date imports through the M9 router; WhatsApp bodies never enter
  candidates/logs/errors and self participation remains implicit in the unchanged candidate contract;
- a desktop-only Obsidian plugin using stable person ids and shell-free bounded CLI subprocesses, with typed
  database/encryption settings, a committed Node lockfile, and deterministic mirrored release artifacts.

**Status:** Delivered.

## M15 — Data quality, insight, and credibility

**Goals:** keep long-lived databases trustworthy, make the privacy story inspectable, and provide publishable
evidence and narratives.

**Deliverables:**

- `pctx doctor`: report-only deterministic duplicate/contradiction/soft-deleted-reference findings with
  structured id-based CLI/MCP suggested actions, never shell-interpolated or auto-applied;
- `pctx stats`: versioned aggregate-only local inventory including sensitivity/audit/changelog summaries,
  disclosure-gate state, path redaction, and main+WAL+SHM storage size;
- additive transliteration-aware `match_detail` while preserving exact-match reason/ranking/ambiguity;
- a fictional-data, locally runnable evaluation plus dated results and use-case gallery.

**Status:** Delivered.

## M16 — First-class CLI import workflow

**Goals:** make the existing review-gated import subsystem directly usable from `pctx` by both humans and
automation, without inventing another import policy or adding model/network dependencies.

**Deliverables:**

- `pctx import stage SOURCE PATH [--self-sender TEXT] [--json]` over the seven existing import sources;
- `pctx import review BATCH_ID [--json]` with deterministic review-safe rendering;
- `pctx import commit BATCH_ID --all|--accept ... [--json]` with explicit acceptance and unchanged unresolved
  dependency semantics;
- stable v1 `people-context-import-batch`, `people-context-import-review`, and
  `people-context-import-commit` JSON documents;
- reuse of the general CLI import workflow/rendering from `pctx init` without changing onboarding safety;
- parser/CLI/runtime/privacy/subprocess coverage and documentation of the local/offline lifecycle.

No new importer, candidate type, schema migration, live service integration, raw-text extraction, or general
CLI/MCP parity is part of M16.

**Status:** Delivered — the `pctx import` group exposes the existing stage/review/commit lifecycle over all seven
sources with stable v1 JSON documents, a bounded 64 MiB/100,000-candidate/64 MiB-payload staging budget, and a
SQLite preflight that refuses an oversized legacy batch before any full-batch read; onboarding shares the same
review rendering and candidate selection, and the released MCP and `pctx init` contracts are unchanged.

## M17 — Agent-assisted knowledge extraction

**Goals:** let agents distill meeting transcripts, conversation logs, notes, and other unstructured material into
strict reviewable people-context candidates while keeping semantic reasoning outside the core server.

**Deliverables:**

- additive strict candidate types for observations, inferred traits, and canonical relationships;
- mandatory explicit confidence plus concise evidence note for staged inferred traits;
- commit support for the new candidates through existing audited/changelogged write use cases;
- `pctx import stage-candidates --source SOURCE --input PATH|- [--json]` for agents without MCP access;
- packaged agent guidance distinguishing fact vs observation vs trait, resolving participants through batch-local
  person candidates, and preserving stage → review → explicit commit;
- explicit safeguards against raw-transcript persistence, unsupported sensitive inference, evidence-free traits,
  and an embedded LLM/model dependency.

**Status:** Delivered. M17.1 delivered the three additive strict candidate types, their commit support
through the existing `RecordObservation`/`RecordTrait`/`SetRelationship` use cases, the conditional resource
bounds on any MCP request that uses one, and ambiguity-preserving person matching for those batches. M17.2
added `pctx import stage-candidates` over that same use case — candidate JSON from a file or stdin, bounded
unconditionally by a 1 MiB read budget plus the M17 count, source-label, and string limits — and extended the
packaged usage skill with the fact/observation/trait extraction workflow. Semantic reasoning stays with the
calling agent: no source text is read, parsed, or stored by People Context.

## M18 — Provenance, idempotency & evidence

**Goals:** make repeated ingestion traceable and duplicate-safe without turning People Context into a document
store, and ground inferred traits in durable evidence records.

**Deliverables:**

- a minimal durable source-session/receipt model with bounded metadata and exact-byte SHA-256 digest, never raw
  source content or default absolute paths;
- concurrency-safe atomic default claiming of source-kind+digest together with source-session/batch/candidate
  staging publication, while explicitly forced reprocessing remains distinct;
- additive bootstrap preservation of staged/partially committed source sessions and the incomplete staging rows
  required to keep their batches reviewable after restore;
- a separate durable record-to-source-session association for committed writes while preserving existing
  message/event-derived `Provenance.session` semantics;
- local source-session inspection showing batch/record ids and status summaries but no source body;
- durable trait-evidence links to observations/interactions involving the same trait subject, including same-batch
  staged evidence resolution and subject validation;
- additive migration/sync/bootstrap/privacy/concurrency coverage for the new provenance/evidence state.

M18 deliberately does not perform semantic record deduplication, automatic confidence recomputation, source
rollback, document storage, or incremental peer replication of incomplete staging state.

**Status:** Planned.

## M19 — Knowledge consolidation & temporal views

**Goals:** make accumulated memory understandable and maintainable over time without introducing an autonomous
belief updater.

**Deliverables:**

- bounded deterministic person timelines over interactions, observations, dated state changes, traits, and M18
  provenance/evidence metadata;
- local CLI timeline output plus ordinary-disclosure MCP access with explicit sensitivity policy;
- a bounded person-scoped consolidation-context read exposing duplicate/superseding/reinforcing/contradictory
  knowledge and the evidence/provenance needed to reason about it;
- an agent maintenance workflow that proposes structured corrections/merges/supersession actions, explains its
  evidence, and waits for explicit user approval before using existing mutation tools;
- deterministic bounds/order/privacy tests proving all analysis/report paths remain read-only.

M19 does not automatically merge records, rewrite traits, recalculate confidence, or run a maintenance daemon.

**Status:** Planned.

## M20 — Streaming importer parsing

**Goals:** close the one resource gap M16 left open — every extractor turns a whole in-budget source into
intermediate Python objects before the first candidate exists, so the candidate ceiling cannot meter it — and
do it across all seven sources and both surfaces at once rather than site by site.

**Deliverables:**

- a narrow parser-work budget bounding live retained parsed records, defaulting to unbounded so existing
  callers are unaffected, alongside a bounded streaming line/record source reader;
- streaming conversions for all seven sources: lazy unfold/split for vCard and iCalendar, streamed CSV input
  for LinkedIn and Outlook, metered address expansion for email, a lazily consumed mailbox for `mbox`, and a
  bounded resolution for WhatsApp;
- the same bound extended to the released MCP `import_content` path **by streaming rather than by rejection**,
  so no source accepted today stops being accepted;
- a preserved-or-explicitly-renegotiated answer for WhatsApp's documented whole-file day/month ordering
  inference, which is the only place a memory fix could otherwise change what is extracted;
- table-driven equivalence tests proving byte-identical candidates, ordering, refs, skip reasons, and indexes
  for every source, plus retention tests proving candidate-free input stays bounded.

M20 adds no source type, candidate type, migration, or new user-visible limit, and does not change the M16
source-byte, candidate, staged-payload, or batch-read ceilings.

**Status:** Planned.

## Post-roadmap candidates

The following remain candidates, not commitments:

- incremental two-way sync replay, pairing, relay, and peer cursors between two already-diverged devices
  (M11 covers only bundle export and empty-database bootstrap restore);
- multi-user ownership and sharing;
- authenticated remote transport;
- reminder notification daemon (M13 ships only a pull-based calendar-feed export);
- read-only local web viewer (`pctx browse`; M14's Obsidian plugin covers browsing for Obsidian users);
- CardDAV synchronization (M14 ships only one-way vCard export);
- watched-folder/background ingestion orchestration outside the core import transaction;
- source-session rollback/retraction after safe lifecycle semantics are designed from real usage.

See `docs/specs/` for one implementation spec per M8–M20 milestone, and
[docs/specs/pr-plan.md](specs/pr-plan.md) for the per-PR implementation checklist derived from those specs.
