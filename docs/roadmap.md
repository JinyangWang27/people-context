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

## Post-roadmap candidates

The following remain candidates, not commitments:

- sync exchange, pairing, replay, peer cursors, and trusted bootstrap restore;
- multi-user ownership and sharing;
- authenticated remote transport;
- SQLCipher at rest;
- reminder notification daemon;
- PyPI packaging and a v1.0 release.
