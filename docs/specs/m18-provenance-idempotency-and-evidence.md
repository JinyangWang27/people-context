# M18 — Provenance, idempotency, and evidence

Status: Planned. See [docs/roadmap.md](../roadmap.md#m18--provenance-idempotency--evidence).

## Motivation

Once humans and agents can routinely import structured exports and distill unstructured material, duplicate
processing and provenance become operational concerns. Re-reading the same meeting transcript should not silently
create a second copy of the same observations, and a derived trait should be explainable in terms of the evidence
that motivated it rather than surviving as a free-floating conclusion.

M18 adds a minimal source/session layer that records *that* material was processed without storing the material
itself, makes repeat ingestion detectable, and lets inferred traits point to durable evidence records. The goal is
traceability and safe idempotency, not document management.

## Scope

In scope:

- first-class local source/ingestion receipts containing bounded metadata and a content digest, never raw source;
- association of a staged import batch with one source session;
- propagation of source-session provenance into records committed from that batch;
- duplicate-source detection before creating another staging batch;
- CLI inspection of source sessions and their derived records/batches;
- durable evidence links from traits to observations/interactions that support them;
- deterministic, reviewable behavior across structured-file and agent-extracted candidate workflows.

Non-goals:

- storing, indexing, embedding, or serving source files/transcripts;
- file synchronization, document management, or a watched-folder daemon;
- cryptographic proof of authorship or tamper-evident external archives;
- automatic rollback/deletion of everything derived from one source;
- automatic trait confidence updates from evidence count;
- semantic consolidation or contradiction resolution (M19);
- treating a content hash as proof that two semantically equivalent but byte-different files are identical.

## Source-session model

Add a small durable source-session/receipt entity. Exact naming is implementation-time, but the conceptual fields
are:

- stable `id`;
- `source_kind` such as `linkedin`, `ics`, `meeting_transcript`, or another caller-defined bounded label;
- optional user/caller label for human inspection;
- SHA-256 digest of the exact source bytes when a source artifact exists;
- optional externally supplied stable source id when the caller has one;
- created/processed timestamp;
- associated staging batch id when applicable;
- status sufficient to distinguish staged/partially committed/committed without duplicating candidate-row truth.

Do **not** store:

- source body/content;
- copied transcript excerpts;
- attachments;
- absolute filesystem paths by default;
- model prompts/responses;
- credentials or external service tokens.

A digest is an idempotency key, not a privacy transform; documentation must still treat it as metadata about
personal source material.

## Idempotency policy

### Structured file imports

For M16 `pctx import stage SOURCE PATH`, compute SHA-256 over the exact file bytes before extraction. If an existing
source session with the same source kind and digest already owns a staging/import history, default behavior does not
create a second batch. Return/report the existing source/session and batch state instead.

An explicit implementation-time override such as `--force` may permit intentional reprocessing, but it must be
clearly named, non-default, and create a distinct source session while retaining the same digest. The exact flag is
chosen in implementation only if a real workflow requires it; duplicate-safe default behavior is binding.

### Agent-extracted sources

`pctx import stage-candidates` / MCP staging may accept optional source-session metadata including a digest computed
by the calling agent over the source artifact. When no digest is supplied, the workflow remains valid but cannot
claim source-level idempotency.

People Context must never hash raw text that it has not been given. The agent remains responsible for reading the
source and may provide the exact-byte digest when its environment can do so deterministically.

### Candidate-level duplicates

M18 does not attempt semantic candidate deduplication. Two different sources may legitimately assert the same fact
or observation. Source-level idempotency prevents accidental reprocessing of one artifact; knowledge consolidation
is a separate M19 concern.

## Provenance propagation

A source session should become the stable ingestion/session provenance anchor for records created from its batch.
Use the existing provenance/session concept where possible rather than inventing parallel provenance fields on each
domain model.

Implementation should prefer:

- `source` continuing to describe the import/extraction surface;
- `session` carrying the durable source-session id for writes created by batch commit;
- optional source-local message/event ids remaining candidate metadata only where needed for review/dedup semantics.

If preserving an existing `message_id`-based session value is a compatibility requirement, introduce the smallest
explicit association needed rather than silently changing provenance semantics. The spec's invariant is that every
committed candidate can be traced back to one durable source session without storing the raw source.

## Trait evidence links

Traits are inferred state and should be able to reference durable evidence. Add a narrow evidence relation from a
trait to one or more existing observations/interactions.

Conceptually:

```text
source session
   ├── interaction A ──┐
   ├── observation B ──┼──> trait T
   └── observation C ──┘
```

Requirements:

- only supported evidence entity types may be linked;
- linked records must exist and be active under existing lifecycle semantics;
- links are deterministic and id-based;
- deleting/correcting evidence must not silently rewrite the trait text or confidence;
- retrieval should be able to explain a trait with evidence ids and concise evidence metadata while respecting
  disclosure/sensitivity rules;
- `evidence_note` remains useful human-readable context and is not removed merely because evidence ids exist.

For same-batch agent extraction, a staged trait may reference staged observation/interaction candidate ids. Commit
must resolve those to durable ids only after the evidence records commit. If any accepted required evidence cannot
resolve, the trait remains unresolved rather than dropping the link or guessing.

Do not allow a trait to cite another trait as evidence in M18; this keeps inference grounded in observed/interacted
material and avoids recursive belief chains.

## Source inspection

Expose local inspection sufficient to answer “where did this come from?” without becoming a document browser.
Conceptual CLI:

```text
pctx sources [--json]
pctx source show SOURCE_SESSION_ID [--json]
```

A source detail may include:

- id, kind, optional label, digest, timestamps/status;
- staging batch id;
- candidate counts/status summaries;
- committed record ids/types derived from the source.

It never returns raw source content. Stable JSON should be versioned from first release if documented for agents.
Human output may render concise provenance paths.

## Migration needs

Expected additive migration(s):

- source-session/receipt table;
- association from staging batch/rows to source session, depending on the existing staging schema;
- trait-evidence relation table with foreign keys/indexes appropriate to supported lifecycle behavior.

Use the next free migration number at implementation time. Every new durable write participates in the established
atomic audit/changelog seam unless the row is explicitly derived/non-replicated by documented design; prefer
replicable primary state for source/evidence metadata that affects user-visible provenance.

## CLI / MCP surface changes

Expected additive changes:

- M16 file staging reports duplicate-source state and source-session id;
- M17 candidate staging optionally accepts source-session metadata/digest;
- local source list/show CLI;
- trait/context representations may gain additive evidence metadata;
- no raw-source retrieval tool.

Do not break existing import envelopes; add fields only where allowed by the M12 compatibility promise or introduce
new versioned JSON documents when a new machine surface is required.

## Security and privacy

- Source receipts are metadata about personal material and must be treated as sensitive local state.
- A SHA-256 digest is not anonymization and must not be presented as such.
- Absolute source paths are not persisted by default because they can disclose usernames, organizations, project
  names, and machine layout.
- Evidence retrieval respects existing sensitivity/disclosure gates; a trait must not reveal restricted evidence to
  an ordinary MCP caller merely because the trait is visible.
- No raw source text enters logs, audit payloads, changelog payloads, errors, or source-session rows.
- Duplicate detection happens locally without external lookups.

## Testing strategy

- Migration tests cover fresh and upgraded databases, FK integrity, indexes, and bootstrap/sync compatibility.
- Exact-byte digest tests cover deterministic hashing, same-source duplicate detection, source-kind scoping, and
  intentional distinct sessions where supported.
- Structured and agent-extracted import tests prove one batch/source association and no duplicate batch by default.
- Provenance tests prove every committed candidate traces to the correct source session without raw content.
- Evidence-link tests cover same-batch observation/interaction resolution, missing/unaccepted evidence, lifecycle
  edge cases, stable ordering, and sensitivity filtering.
- Source list/show tests contain only bounded metadata/ids and never path/body sentinels.
- Sync/bootstrap/export tests explicitly account for the new durable state according to the chosen replication
  policy.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- Idempotency is source-level first; semantic record consolidation is intentionally left to M19.
- Source sessions are receipts/provenance anchors, not a hidden document store.
- Trait evidence is grounded only in observations/interactions in M18.
- Automatic source rollback and automatic confidence recomputation remain deferred until real usage demonstrates
  safe semantics.
