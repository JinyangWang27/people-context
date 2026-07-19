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
- narrow `GraphReader` port with cycle-safe recursive SQLite CTEs;
- read-only `get_relationship_graph` and `find_connection` MCP tools with depth/node/edge caps and explicit
  truncation/not-found/not-connected contracts;
- CLI-only deterministic Obsidian vault export with marker-file ownership safety, Unicode/collision-safe names,
  organization hubs, perspective Dataview/wikilinks, durable facts/reminders, and explicit sensitivity opt-in;
- fake-port, real-SQLite, in-memory MCP, CLI, migration, and real-stdio E2E coverage.

**Status:** Delivered.

## M8 — Distribution & reach

**Goals:** cut the distance between "hears about this project" and "has it running in a client" to a single
command, without adding runtime dependencies or changing any server behavior.

**Deliverables:**

- verified zero-clone install: `uvx people-context-mcp` against the existing PyPI-published package (build
  and trusted-publish flow already documented in [docs/releasing.md](releasing.md));
- a `server.json` following the official MCP Registry packages schema (`registryType: "pypi"` plus stdio
  transport, with the required `mcp-name:` README ownership marker) published via `mcp-publisher`, and
  equivalent metadata submitted to community directories (Smithery, PulseMCP, mcp.so, Glama);
- a Claude Desktop extension (`.mcpb` bundle) wrapping the same stdio invocation pattern already used by
  `.claude-plugin/mcp.json`;
- documented one-line stdio configs for Claude Desktop, Cursor, Windsurf, and VS Code in the README, alongside
  the existing generic stdio configuration and Claude Code instructions;
- an optional Docker image (loopback stdio, bind-mounted database volume) built and published by CI, following
  the existing publish-workflow pattern already used for PyPI (`release.yml`) and the OpenClaw plugin
  (`package-publish.yml`);
- README quick-start and docs-table updates reflecting the new install paths.

No `domain`, `app`, `ports`, or MCP tool-surface changes are required; this milestone is packaging, metadata,
and CI only.

**Status:** Planned.

## M9 — Cold start & onboarding

**Goals:** give a freshly installed, empty database something to show in under a minute, and broaden the
extract-and-stage import pipeline to the contact sources people actually export from.

**Deliverables:**

- `people-context init`: an interactive CLI onboarding command that optionally runs the existing vCard import
  flow (`ImportContent` → `ReviewImport` → `CommitImport`, unchanged) against a Google/Apple/macOS Contacts
  export, seeds a self person through the existing `RememberPerson`/`RememberPersonInput(is_self=True)` path,
  and prompts for an initial communication philosophy via the existing `SetCommunicationPhilosophy` use case;
- `people-context demo`: seeds a small fictional dataset into a dedicated demo database (never the user's real
  `--db`/resolved path) using the same `RememberPerson`/`SetRelationship`/`RecordInteraction` use cases, then
  prints example `resolve_person`, `get_relationship_graph`, and `find_connection` invocations to try;
- two new import sources reusing the existing candidate vocabulary with zero changes to `domain`, `app`
  contracts, `import_staging`, or the review/commit tools: `.ics` calendar attendees (`source_type="ics"`) and
  a LinkedIn connections export (`source_type="linkedin"`);
- `ImportExtractorRouter` (currently defined in `adapters/vcard_import.py`) moves to its own adapter module
  before the third source type lands, so source dispatch no longer lives inside one source's module;
- fake-port, real-SQLite, in-memory MCP, CLI, and stdio E2E coverage for both new sources and the new CLI
  commands.

**Status:** Planned.

## M10 — Agent utilization

**Goals:** make the existing tool surface easier for agents to use correctly and consistently — this milestone
adds no server code, since `resolve_person`, `get_communication_guidance`, and `stage_candidates` already exist
and are already wired into `ToolDeps`.

**Deliverables:**

- a packaged Claude Code skill describing when to call `resolve_person`, `get_communication_guidance`, and the
  stage/review/commit import flow, shipped at the plugin root (`skills/`), where Claude Code discovers it;
- user-invocable who/remember/reminders entry points (namespaced as `/people-context:who` etc.) wrapping
  `resolve_person`/`get_person_context`, `remember_person`, and `list_reminders` respectively;
- an end-of-session capture instruction in the skill (deliberately no hook: `SessionEnd` cannot inject
  prompts and a `Stop` hook fires every turn) asking the agent to propose staged candidates via
  `stage_candidates` for anything durable learned in the session — never an automatic `commit_import`;
- at most a small, additive extension of the `SERVER_INSTRUCTIONS` string in `adapters/mcp/server.py` mentioning
  `get_communication_guidance` and `stage_candidates`, with no tool signature or behavior change.

**Status:** Planned.

## M11 — Sync bundle export and trusted bootstrap restore

**Goals:** give the M6 changelog foundations a first consumer beyond local inspection — a file-based bundle
that moves one device's complete state to a brand-new device, doubling as a backup. Incremental two-way replay
between two independently-diverged devices remains the harder, deliberately deferred part of
[the sync design](design/sync.md).

**Deliverables:**

- `people-context sync push --output DIR`: a CLI-only, read-only bundle export that reads the portable
  snapshot (including relationship vocabulary), every referenced device row, the complete changelog, and the
  originating device's HLC watermark inside one read transaction, and writes them as one versioned JSON
  bundle file;
- `people-context sync pull --input PATH`: a CLI-only, trusted bootstrap restore that only ever targets a
  freshly initialized, still-empty database — never a two-way merge — and, in one atomic `BEGIN IMMEDIATE`
  transaction, verifies emptiness, writes relationship vocabulary, retired device history, primary rows, and
  changelog, rebuilds FTS, and advances the local device's HLC past the bundle watermark; imported device
  identities are never active on the restored machine, and only the optional semantic reindex runs outside
  the transaction;
- an additive widening of `Changelog.list_entries`'s `limit` parameter to accept `None` for "all entries",
  needed because bundle export must not silently cap history at the current default of 100;
- explicit CLI refusal (no partial/best-effort merge) when the pull target already has primary data, with a
  clear message pointing at the still-deferred incremental-replay work;
- fake-port, real-SQLite, CLI, and stdio E2E coverage, including a push-from-A/pull-into-B round trip asserting
  content parity and HLC continuity.

**Status:** Planned.

## M12 — Trust, stability, and v1.0

**Goals:** formalize the compatibility discipline the project has informally followed since M7 (see the
`duplicate_relationships_removed` precedent in the README), and close the two most-requested trust gaps: at-rest
encryption and a stated threat-model comparison against cloud memory tools.

**Deliverables:**

- a written MCP response-contract and DB-schema compatibility promise (additive-only fields within a major
  version, stable tool names/required parameters, forward-only additive migrations under
  `adapters/sqlite/migrations/`);
- `pyproject.toml` version bump to `1.0.0` and a "first 1.0" addition to the existing release checklist in
  [docs/releasing.md](releasing.md);
- opt-in SQLCipher at-rest encryption behind a new optional dependency extra (alongside the existing `semantic`
  extra) and an explicit `PEOPLE_CONTEXT_DB_KEY`-gated open path that leaves the default, unencrypted `open_db`
  behavior and its entire existing test suite unchanged;
- a threat-model comparison section appended to the existing "Threat model notes" in
  [docs/privacy-and-safety.md](privacy-and-safety.md), contrasting local-first storage with cloud memory tools
  (mem0, Zep, and similar);
- README polish: a short screenshot/GIF funnel built from the M9 `people-context demo` walkthrough.

**Status:** Planned.

## Post-roadmap candidates

The following remain candidates, not commitments:

- incremental two-way sync replay, pairing, relay, and peer cursors between two already-diverged devices
  (M11 covers only bundle export and empty-database bootstrap restore);
- multi-user ownership and sharing;
- authenticated remote transport;
- reminder notification daemon;
- read-only local viewer (`people-context browse`);
- vCard export and CardDAV, complementing the existing vCard import.

See `docs/specs/` for one implementation spec per M8–M12 milestone.
