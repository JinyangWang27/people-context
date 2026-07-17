# Roadmap

This document lists the milestones for `people-context-mcp`, from the initial scaffold through sync
groundwork. Milestones are additive: each builds on the previous one's schema and architecture without
requiring rework, per the hexagonal design described in [docs/architecture.md](architecture.md).

## M0 — Foundation (this scaffold)

**Goals:** stand up the project skeleton, the full data model, and a working end-to-end vertical slice
proving the architecture holds together.

**Deliverables:**
- Repository layout, documentation set (this document and its siblings).
- Full domain model (`domain/`): `Person`, `Alias`, `Relationship`, `Organization`, `Affiliation`, `Fact`,
  `Observation`, `Trait`, `Interaction`, `Reminder`, preferences, shared value objects.
- SQLite schema (`adapters/sqlite/migrations/001_initial.sql`) covering every table in
  [docs/data-model.md](data-model.md), including `traits`, `reminders`, and `user_preferences`.
- A stdio MCP server (`adapters/mcp/server.py`) with the full tool surface registered — `resolve_person`,
  `search_people`, and `remember_person` implemented against the real SQLite repository; the remaining tools
  registered as typed stubs (see [docs/mcp-interface.md](mcp-interface.md)).
- A view/search `people-context` CLI (`db-path`, `list`, `search`, `show`, `export`).
- Tests: domain value objects, resolution pipeline stages, SQLite repository round-trips.

**Status:** Delivered. The initial scaffold established the complete schema, hexagonal boundaries, stdio MCP
surface, CLI, and tested SQLite vertical slice on which M1–M5 build.

## M1 — Identity and retrieval (delivered)

**Goals:** complete identity resolution and bring context retrieval up to its designed minimal-disclosure
behaviour.

**Deliverables:**
- Full resolution pipeline (all 5 stages from [docs/identity-resolution.md](identity-resolution.md)),
  including fuzzy matching (stage 4) and hint boosting (stage 5).
- `get_person_context` implemented with relevance ranking and minimal disclosure
  (`purpose`, `max_items`, `include_sensitive` honoured — see
  [docs/mcp-interface.md](mcp-interface.md#minimal-disclosure-in-get_person_context)).

**Status:** Delivered. The MCP server and CLI share the same SQLite-backed retrieval use case; coverage
includes fake use-case tests, SQLite hydration, in-memory MCP tests, and a real stdio SDK/CLI round trip.

## M2 — Full write surface, curation, and communication guidance

**Goals:** make every record type writable, and deliver the communication-guidance and reminder features.

**Deliverables:**
- All record tools: `add_alias`, `set_relationship`, `set_affiliation`, `record_fact`,
  `record_observation`, `record_trait`, `record_interaction`.
- `correct_record` for correcting previously recorded assertions.
- Provenance enforced consistently across every write path.
- `get_communication_guidance` and `set_communication_philosophy` (see
  [docs/communication-guidance.md](communication-guidance.md)).
- Reminders: `set_reminder`, `complete_reminder`, `list_reminders`.
- Audit log polish (consistent payload shapes across all op types).

**Status:** Delivered. All M2 tools are SQLite-backed and covered by fake-port unit tests, real-SQLite
integration tests, in-memory MCP tests, and a real stdio write-to-read round trip.

## M3 — Lifecycle and import

**Goals:** support the full lifecycle of a record — merging duplicates, forgetting data, exporting it — and
bring in the first external content source.

**Deliverables:**
- `merge_people` (re-parents related rows, keeps a full audit trail).
- `forget` (hard delete + audit tombstone).
- `export_data` (full JSON export).
- Email import, extract-and-stage (`import_content`, `review_import`, `commit_import`; `.eml`/mbox files) —
  see [docs/import.md](import.md).
- Full CLI edit/curation commands: `edit`, `add-alias`, `set`, `delete`, `reindex` — see
  [docs/cli.md](cli.md).

**Status:** Delivered. All six lifecycle/import MCP tools are implemented, destructive multi-row operations
are transactional, email/mbox extraction is header-only, and the CLI shares the same application use cases.

## M4 — Transport and retrieval upgrades

**Goals:** broaden how the server can be reached and how relevant content is found.

**Deliverables:**
- Localhost Streamable HTTP transport adapter, alongside the existing stdio adapter (see
  [docs/architecture.md](architecture.md#how-new-transports-and-importers-slot-in)).
- Optional `sqlite-vec` embeddings for semantic person/interaction retrieval, layered onto the existing
  SQLite file (see [docs/decisions/0002-sqlite.md](decisions/0002-sqlite.md)).
- More importers: contacts/vCard, notes.

**Status:** Delivered. Stdio remains the default and the same registered server can run as unauthenticated,
loopback-only Streamable HTTP. The optional semantic extra uses the pinned multilingual Model2Vec model and
a cosine `sqlite-vec` table in the primary SQLite file; only explicit semantic reindex may download it.
vCard 3.0/4.0 imports report per-card skips, while `stage_candidates` lets an agent extract strict candidates
from user-provided notes without persisting the notes themselves.

## M5 — Sync groundwork

**Goals:** lay the design groundwork for syncing one user's data across devices and, eventually, sharing data
between users, without committing to an implementation.

**Deliverables:**
- A replication design grounded in the current audit, lifecycle, export, and privacy behaviour — see
  [docs/design/sync.md](design/sync.md).
- A proposed dedicated changelog because the current accountability audit is intentionally lossy and is
  rewritten by forget — see
  [docs/decisions/0004-changelog-vs-audit-log.md](decisions/0004-changelog-vs-audit-log.md).
- A proposed conservative conflict policy that preserves identity correctness and makes forget dominant — see
  [docs/decisions/0005-conflict-resolution-strategy.md](decisions/0005-conflict-resolution-strategy.md).
- Multi-user ownership, actor, `is_self`, and sensitivity considerations, design only.

**Status:** Delivered as a design. M5 adds documentation and proposed ADRs only; it includes no sync runtime,
transport, schema migration, dependency, or multi-user implementation.

## Post-roadmap candidates

The following are candidates for future work, not commitments:

- sync implementation;
- multi-user ownership and sharing;
- authenticated remote transport;
- SQLCipher at rest;
- reminder notification daemon;
- PyPI packaging and a v1.0 release.
