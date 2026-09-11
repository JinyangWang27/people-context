# Roadmap

Milestones are additive and preserve the hexagonal dependency rule. Completed entries summarize outcomes;
linked documents retain implementation details and acceptance criteria.

## M0 — Foundation

Delivered the domain/schema scaffold, SQLite repository, stdio MCP server, initial identity tools, CLI,
and vertical-slice tests.

**Status:** Delivered.
**Details:** [Documentation](architecture.md).

## M1 — Identity and retrieval

Delivered explainable identity resolution and bounded, sensitivity-aware person context.

**Status:** Delivered.
**Details:** [Documentation](mcp-interface.md).

## M2 — Full write surface, curation, and communication guidance

Delivered record writes, corrections, reminders, communication guidance, provenance, and audited mutations.

**Status:** Delivered.
**Details:** [Documentation](communication-guidance.md).

## M3 — Lifecycle and import

Delivered merge, forget, JSON export, reviewable email/mbox import, and CLI curation.

**Status:** Delivered.
**Details:** [Documentation](import.md).

## M4 — Transport and retrieval upgrades

Delivered loopback HTTP, optional pinned semantic retrieval, vCard import, and strict agent-candidate staging.

**Status:** Delivered.
**Details:** [Documentation](mcp-interface.md).

## M5 — Sync groundwork

Designed replication, conflict handling, and ownership/sharing boundaries; no sync runtime.

**Status:** Delivered as design only.
**Details:** [Documentation](design/sync.md).

## M6 — Sync foundations

Delivered installation identity, persisted HLC, replayable changelog, atomic audited writes, and `sync-log`.
Peer exchange and incremental replay remain deferred.

**Status:** Delivered.
**Details:** [Documentation](design/sync.md).

## M7 — Relationship graph & vault export

Delivered canonical relationship vocabulary, bounded graph traversal, and safe CLI-only Obsidian vault export.

**Status:** Delivered.
**Details:** [Documentation](relationship-graph.md).

## M8 — Distribution & reach

Delivered zero-clone installation, Registry metadata, a Desktop bundle, editor configurations, and Docker packaging.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m8-distribution-and-reach.md).

## M9 — Cold start & onboarding

Delivered `pctx init`, an isolated fictional demo, and calendar/LinkedIn imports through the shared import router.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m9-cold-start-and-onboarding.md).

## M10 — Agent utilization

Delivered shared usage guidance and who/remember/reminders skills over existing tools, preserving reviewed capture.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m10-agent-utilization.md).

## M11 — Sync bundle export and trusted bootstrap restore

Delivered file-based snapshot export and atomic empty-database bootstrap restore with private file output.
Incremental synchronization between diverged devices remains deferred.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m11-sync-bundle-and-bootstrap-restore.md).

## M12 — Trust, stability, and v1.0

Delivered the compatibility promise, optional SQLCipher encryption, privacy comparisons, and synchronized v1.0
server metadata through release automation; integration versions remain independent.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m12-trust-stability-v1.md).

## M13 — Daily utility & proactive signals

Delivered stale-relationship and upcoming-date reports, meeting preparation, reminder calendar export, and local
changelog watching. Reminders remain pull-based.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m13-daily-utility.md).

## M14 — Ecosystem & interoperability

Delivered portable person briefs, one-way vCard export, Outlook/WhatsApp imports, and a live desktop Obsidian plugin.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m14-ecosystem-interop.md).

## M15 — Data quality, insight, and credibility

Delivered report-only `doctor`, aggregate `stats`, transliteration match explanations, fictional evaluations,
and worked use cases.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m15-data-quality-and-credibility.md).

## M16 — First-class CLI import workflow

Delivered CLI import stage/review/commit with stable v1 JSON, bounded staging, and shared onboarding review.
Existing MCP and onboarding contracts are preserved.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m16-first-class-cli-import.md).

## M17 — Agent-assisted knowledge extraction

Delivered staged observations, evidence-backed traits, relationships, and CLI candidate staging.
Agents distill source material; the server validates concise candidates and preserves explicit reviewed commit.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m17-agent-assisted-knowledge-extraction.md).

## M18 — Provenance, idempotency & evidence

Delivered duplicate-safe source receipts, commit mappings, bounded source inspection, and durable trait evidence,
with merge/forget, disclosure, and backward-compatible bootstrap integration.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m18-provenance-idempotency-and-evidence.md).

## M19 — Knowledge consolidation & temporal views

Delivered bounded timelines, consolidation context, atomic fact supersession, and reviewed maintenance guidance.
No automatic belief revision or record merging.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m19-consolidation-and-temporal-views.md).

## M20 — Streaming importer parsing

Delivered streaming import parsing and bounded retained parser state across all seven sources and MCP import,
preserving extraction behavior and existing user-visible limits.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m20-streaming-importer-parsing.md).

## M21 — Adoption & agent ergonomics

Delivered client setup, one-call `remember`, name-based reads, typed tool schemas, and shared MCP prompts/resources.
Directory publication and a recorded real-model evaluation remain account-owner steps.

**Status:** Delivered in the repository.
**Details:** [Documentation](distribution-checklist.md).

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

Detailed delivery contracts remain in [the milestone specs](specs/) and the
[PR checklist](specs/pr-plan.md).

## M22 — Source-grounded CV and background capture

Delivered attributed CV/background capture through existing staging, preserving uncertain dates, sensitivity,
and source claims without raw-document storage.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m22-source-grounded-person-capture.md).

## M23 — Explainable person briefs and history

Delivered opt-in brief history with explicit bounds, date meanings, source references, and disclosure limits.
No separate biography store or profile endpoint.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m23-explainable-person-history.md).

## M24 — Conservative CV update review

Delivered affiliation-aware consolidation and reviewed CV updates that preserve history, uncertainty, and
explicit acceptance; omission never implies deletion.

**Status:** Delivered.
**Details:** [Milestone spec](specs/m24-reviewed-person-updates.md).

## M25 — Relationship-aware communication coaching

**Status:** Delivered. M25.1 delivered the `communication-coach` skill and mirrored its essential workflow into
the shared usage guidance and the packaged `people-context://guide`; M25.2 added the fictional bilingual scenarios
and the human-review rubric. No coaching review has been recorded yet, so nothing here claims effectiveness.

**Deliverables:** a discoverable communication-coach skill, shared/packaged guide integration, and fictional
Chinese and English scenarios for work, friends, and family. Provide immediate help plus a short explanation;
support practice and reflection on request. Preserve uncertainty, the user's voice and boundaries, and optional
reviewed capture. No automatic personality inference, sending, learning profile, or server-side LLM.

M25.1 delivers the workflow; M25.2 delivers examples and qualitative evaluation. Reuse existing guidance,
context, and capture. M25 is independent of M26.

**Spec:** [M25 — Communication coaching](specs/m25-communication-coaching.md).
**PRs:** [M25 checklist](specs/pr-plan.md#m25--relationship-aware-communication-coaching).

## M26 — Attribution-aware transcript review

**Status:** In progress. M26.1 delivered the `transcript-review` skill and mirrored its essential rules into the
shared usage guidance and the packaged `people-context://guide`; the fictional partial-capture scenarios and the
lifecycle checks are still to come.

**Deliverables:** a discoverable transcript-review skill, shared/packaged guide integration, and fictional partial
capture scenarios with lifecycle checks. Review statement attribution conversationally: several labels may identify
one person, and one room-microphone label may contain several people. Confirmed participation does not establish
who spoke or accepted a task. Stage supported claims only; unresolved attribution stays in conversation.

M26.1 delivers the workflow; M26.2 proves supported partial capture and documents it. Reuse M17/M18 extraction
and provenance plus M22 attribution. No resumable review state, raw transcript storage, acoustic identification,
new candidate type, or native Ideashell integration. M26 is independent of M25.

**Spec:** [M26 — Transcript attribution review](specs/m26-transcript-attribution-review.md).
**PRs:** [M26 checklist](specs/pr-plan.md#m26--attribution-aware-transcript-review).
