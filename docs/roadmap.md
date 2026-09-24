# Roadmap

Every milestone in the table is delivered; the linked documents describe current behavior, and the specs that
drove them live in git history. Planned milestones keep their spec in [specs/](specs/) until they ship.

| Milestone | Outcome | Details |
|---|---|---|
| M0 — Foundation | Domain/schema scaffold, SQLite repository, stdio MCP server, identity tools, CLI. | [architecture](architecture.md) |
| M1 — Identity and retrieval | Explainable identity resolution and bounded, sensitivity-aware person context. | [identity](identity-resolution.md) |
| M2 — Write surface and guidance | Record writes, corrections, reminders, communication guidance, provenance, audited mutations. | [guidance](communication-guidance.md) |
| M3 — Lifecycle and import | Merge, forget, JSON export, reviewable email/mbox import, CLI curation. | [import](import.md) |
| M4 — Transport and retrieval | Loopback HTTP, optional pinned semantic retrieval, vCard import, strict agent-candidate staging. | [MCP interface](mcp-interface.md) |
| M5 — Sync design | Replication, conflict handling, and ownership boundaries, as design only. | [sync design](design/sync.md) |
| M6 — Sync foundations | Installation identity, persisted HLC, replayable changelog, atomic audited writes, `sync-log`. | [sync design](design/sync.md) |
| M7 — Relationship graph and vault export | Canonical relationship vocabulary, bounded graph traversal, Obsidian vault export. | [relationship graph](relationship-graph.md) |
| M8 — Distribution | Zero-clone install, MCP Registry metadata, Desktop bundle, editor configs, Docker. | [desktop and editors](desktop-and-editors.md) |
| M9 — Cold start | `pctx init`, an isolated fictional demo, calendar and LinkedIn imports. | [CLI](cli.md) |
| M10 — Agent utilization | Shared usage guidance and who/remember/reminders skills. | [skills](../skills/) |
| M11 — Sync bundle | File-based snapshot export and atomic empty-database bootstrap restore (`pctx sync push`/`pull`). | [moving laptops](use-cases/moving-to-a-new-laptop.md) |
| M12 — Trust and v1.0 | Compatibility promise, optional SQLCipher encryption, v1.0 release automation. | [compatibility](compatibility.md) |
| M13 — Daily utility | Stale-relationship and upcoming-date reports, meeting prep, reminder calendar feed, changelog watch. | [CLI](cli.md) |
| M14 — Interoperability | Person briefs, vCard export, Outlook/WhatsApp imports, Obsidian plugin. | [Obsidian plugin](obsidian-plugin.md) |
| M15 — Data quality | Report-only `doctor`, aggregate `stats`, transliteration explanations, fictional evaluations. | [evals](evals.md) |
| M16 — CLI import workflow | `pctx import` stage/review/commit with versioned JSON and bounded staging. | [import](import.md) |
| M17 — Agent-assisted extraction | Staged observations, evidence-backed traits, relationships, CLI candidate staging. | [import](import.md) |
| M18 — Provenance and evidence | Duplicate-safe source receipts, commit mappings, source inspection, durable trait evidence. | [import](import.md) |
| M19 — Consolidation and timelines | Bounded timelines, consolidation context, atomic fact supersession. | [MCP interface](mcp-interface.md) |
| M20 — Streaming importers | Streaming parsing with bounded parser state across every importer and MCP import. | [import](import.md) |
| M21 — Adoption and ergonomics | `pctx setup`, one-call `remember`, name-based reads, typed tool schemas, MCP prompts/resources. | [distribution checklist](distribution-checklist.md) |
| M22 — Source-grounded capture | Attributed CV/background capture through staging, with `stated_by` assertion attribution. | [import](import.md) |
| M23 — Explainable history | Opt-in brief history with explicit bounds, date meanings, and source references. | [MCP interface](mcp-interface.md) |
| M24 — CV update review | Affiliation-aware consolidation and reviewed CV updates; omission never implies deletion. | [import](import.md) |
| M25 — Communication coaching | `communication-coach` skill and fictional bilingual scenarios with a review rubric. | [coaching examples](communication-coaching-examples.md) |
| M26 — Transcript review | `transcript-review` skill and fictional partial-capture scenarios with an attribution rubric. | [transcript examples](transcript-review-examples.md) |
| M27 — Shared database | `~/.pctx/people.db` as the one CLI/MCP default. | [CLI](cli.md#database-location-resolution) |
| M28 — Groups and shared connections | Protected groups and memberships, `pctx group shared` / `explain_shared_connections`, staged group candidates. | [shared connections](shared-connections-examples.md) |
| M29 — Editable staging | Amend/withdraw staged candidates, numbered and ranged review, `pctx import edit` via `$EDITOR`. | [import](import.md) |
| M30 — Local web review | `pctx browse`: loopback page to browse people, review batches, and edit candidates inline. | [CLI](cli.md) |

No coaching or transcript review has been recorded against its rubric yet, so nothing here claims either
workflow's effectiveness. Directory publication and a recorded real-model evaluation remain account-owner steps.

## Planned

### M31 — Grounded perspectives and skill refinement

Add a client workflow for evidence-grounded perspectives on contacts and public figures, refine relevant skills
against recorded baselines, and explicitly export reviewed portable perspective snapshots. Adapt Nuwa's grounded
extraction ideas and Luban's verification discipline while preserving ordinary disclosure, uncertainty, and reviewed
capture. Reuse existing records and tools; the only new CLI surface is a narrow, database-free package publisher.
No new profile store, server API, or server-side LLM is planned.

M31.1 delivered the `person-perspective` skill and mirrored its essential workflow into the shared usage guidance
and the packaged `people-context://guide`. M31.2 delivered the human-review perspective suite, its fictional store,
and a recorded model-backed baseline awaiting human review; M31.3 refines skills
with recorded comparisons; M31.4 adds reviewed client-side export; M31.5 verifies portability and documents examples.
The five PRs are sequential. A narrow CLI publisher writes reviewed snapshots privately and atomically; corrections
or forget operations cannot revoke copies.

**Spec:** [M31 — Grounded perspectives and skill refinement](specs/m31-grounded-perspectives-and-skill-refinement.md).
**PRs:** [M31 checklist](specs/pr-plan.md#m31--grounded-perspectives-and-skill-refinement).

## Candidates

These are candidates, not commitments:

- incremental two-way sync replay, pairing, relay, and peer cursors between two already-diverged devices
  (M11 covers only bundle export and empty-database bootstrap restore);
- multi-user ownership and sharing;
- authenticated remote transport;
- reminder notification daemon (M13 ships only a pull-based calendar-feed export);
- CardDAV synchronization (M14 ships only one-way vCard export);
- watched-folder/background ingestion orchestration outside the core import transaction;
- source-session rollback/retraction after safe lifecycle semantics are designed from real usage.
