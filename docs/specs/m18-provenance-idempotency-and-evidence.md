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
- hash/extraction consistency so a receipt digest always describes the same stable source snapshot that produced
  the staged candidates;
- atomic association of a staged import batch with one source session;
- a separate durable record-to-source-session association that preserves existing per-message/event provenance;
- duplicate-source detection before creating another staging batch;
- bootstrap continuity for incomplete source sessions and their staging rows;
- CLI inspection of source sessions and their derived records/batches;
- durable evidence links from traits to observations/interactions that support the same person;
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
- SHA-256 digest of the exact stable source bytes when a source artifact exists;
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

For M16 `pctx import stage SOURCE PATH`, the digest and candidate extraction must describe the **same stable source
snapshot**. A path is not a snapshot: the file may change between a hash read and a later extractor reopen.

Use one of two implementation patterns depending on the source adapter:

1. For importers that can consume bytes/content directly, read one bounded immutable byte snapshot once, compute
   SHA-256 over those bytes, and extract from those exact same bytes.
2. For an adapter that must retain path-oriented processing (notably the existing `mbox` path-only contract), use
   verified stable-path extraction without creating a raw temporary copy: capture file identity/metadata and a
   pre-extraction SHA-256, run extraction, then capture identity/metadata and SHA-256 again. If identity, size,
   high-resolution modification metadata, or digest differs, discard the extracted candidates and retry a bounded
   number of times or fail with a safe `source_changed_during_import`-style error. No source session, batch, or
   candidate row becomes durable until a stable pass succeeds.

The second pattern is a compatibility seam for path-only parsing, not permission to accept a digest from one file
version and candidates from another. Rehashing after extraction is mandatory; metadata-only checks are insufficient.
A source that is continuously changing must fail rather than stage an indeterminate mixture. Do not solve this by
persisting an unencrypted/raw temporary duplicate of the user's source.

After a stable snapshot/pass exists, the default digest claim, source-session insertion, staging-batch creation,
source-session/batch association, and candidate-row staging must become visible atomically. Two concurrent processes
importing the same source kind and digest must not both create default source sessions/batches.

Use a database uniqueness mechanism plus one transaction (for example `BEGIN IMMEDIATE` around the claim/stage
write) rather than a check-then-insert race. If another process wins the canonical claim, the loser returns/reports
the already-existing source session and batch state without creating any new durable row.

An explicit implementation-time override such as `--force` may permit intentional reprocessing, but it must be
clearly named, non-default, and create a distinct processing session while retaining the same digest. The uniqueness
scheme must distinguish intentional forced reprocessing from the one canonical duplicate-safe default claim.

### Bootstrap and incomplete batches

M11 intentionally did not transfer device-local `import_staging`, but M18 source sessions make a durable
source-session → batch reference user-visible. That reference must never be restored without the rows needed to
review/finish an incomplete batch.

Therefore M18 extends the bootstrap bundle additively to carry source-session state and any associated incomplete
`import_staging` rows needed to preserve staged/partially committed batches. Restore validates those references and
restores them atomically with the other source-session state. Older v1 bundles that do not contain the additive M18
fields remain valid and treat the new collections as empty.

A fully committed source may be represented by its durable source/record associations without retaining staging
rows once existing lifecycle policy considers them unnecessary. A staged or partially committed source must not be
restored into a state where duplicate detection suppresses re-staging yet `review_import` cannot find its batch.

Incremental peer replay of staging state remains out of scope; this requirement is for the existing M11 full-bundle
bootstrap/backup contract.

### Agent-extracted sources

`pctx import stage-candidates` / MCP staging may accept optional source-session metadata including a digest computed
by the calling agent over the source artifact. When no digest is supplied, the workflow remains valid but cannot
claim source-level idempotency.

People Context must never hash raw text that it has not been given. The agent remains responsible for reading the
source and may provide the exact-byte digest when its environment can do so deterministically. A caller-supplied
digest is provenance metadata from that caller; People Context must not imply it independently verified source
bytes that were never provided.

### Candidate-level duplicates

M18 does not attempt semantic candidate deduplication. Two different sources may legitimately assert the same fact
or observation. Source-level idempotency prevents accidental reprocessing of one artifact; knowledge consolidation
is a separate M19 concern.

## Provenance propagation

A source session is an additional durable ingestion anchor; it does **not** replace or repurpose existing
`Provenance.session` semantics.

Existing import behavior that stores source-local message/event identifiers in `Provenance.session` is a stable
contract and remains unchanged. In particular, email message ids, calendar event ids, and any existing candidate
`message_id`-derived session values retain their current meaning.

Add the smallest explicit record-to-source-session association needed to trace each committed candidate to its
source session, conceptually keyed by durable record type/id plus `source_session_id`. That association is written
atomically with the corresponding committed record and participates in normal durability/sync policy. This avoids
parallel provenance fields on every domain model while preserving message-level traceability.

The invariant is:

- `source` continues to describe the import/extraction surface;
- existing `Provenance.session` values keep their existing source-local semantics;
- each record committed from an M18-tracked batch additionally has one durable source-session association;
- source inspection can traverse that association without storing the raw source.

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
- evidence must belong to the trait subject: an observation's `person_id` equals the trait's `person_id`, and an
  interaction's participant ids contain the trait's `person_id`;
- links are deterministic and id-based;
- deleting/correcting evidence must not silently rewrite the trait text or confidence;
- retrieval should be able to explain a trait with evidence ids and concise evidence metadata while respecting
  disclosure/sensitivity rules;
- `evidence_note` remains useful human-readable context and is not removed merely because evidence ids exist.

For same-batch agent extraction, a staged trait may reference staged observation/interaction candidate ids. Commit
must resolve those to durable ids only after the evidence records and trait subject resolve. It then applies the
same subject-ownership rule to the resolved records. If any accepted required evidence cannot resolve or belongs to
a different person, the trait remains unresolved rather than dropping the link or guessing.

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

- source-session/receipt table with a concurrency-safe canonical digest claim for default processing;
- association from staging batch/rows to source session;
- durable record-to-source-session relation preserving existing `Provenance.session` meaning;
- trait-evidence relation table with foreign keys/indexes appropriate to supported lifecycle behavior.

Use the next free migration number at implementation time. Every new durable write participates in the established
atomic audit/changelog seam unless the row is explicitly operational staging state under the documented bootstrap
policy. Source sessions, record-source associations, and trait-evidence metadata that affect user-visible provenance
are replicable primary state. Incomplete staging remains operational state but is carried in full bootstrap bundles
so source-session/batch references cannot become dangling.

## CLI / MCP surface changes

Expected additive changes:

- M16 file staging reports duplicate-source state and source-session id;
- M17 candidate staging optionally accepts source-session metadata/digest;
- local source list/show CLI;
- trait/context representations may gain additive evidence metadata;
- no raw-source retrieval tool.

Do not break existing import envelopes; add fields only where allowed by the M12 compatibility promise or introduce
new versioned JSON documents when a new machine surface is required. Existing provenance fields keep their prior
meaning.

## Security and privacy

- Source receipts are metadata about personal material and must be treated as sensitive local state.
- A SHA-256 digest is not anonymization and must not be presented as such.
- Digest/source attribution is accepted only after stable snapshot verification for file imports; a TOCTOU race
  must not attach digest A to candidates parsed from bytes B.
- Absolute source paths are not persisted by default because they can disclose usernames, organizations, project
  names, and machine layout.
- Evidence retrieval respects existing sensitivity/disclosure gates; a trait must not reveal restricted evidence to
  an ordinary MCP caller merely because the trait is visible.
- Subject validation prevents Alice's trait from exposing Bob-only observation metadata or an interaction in which
  Alice did not participate.
- No raw source text enters logs, audit payloads, changelog payloads, errors, source-session rows, or temporary
  persistent copies created solely for hashing/extraction consistency.
- Duplicate detection happens locally without external lookups.

## Testing strategy

- Migration tests cover fresh and upgraded databases, FK integrity, indexes, and bootstrap/sync compatibility.
- Exact-byte digest tests cover deterministic hashing, same-source duplicate detection, source-kind scoping, and
  intentional distinct forced sessions where supported.
- Snapshot-consistency tests prove byte-capable importers hash and parse the exact same immutable bytes.
- Concurrent-modification tests change a source between/during hash and extraction and prove no durable receipt or
  staging batch is created from mismatched bytes. Include the path-only `mbox` case and assert stable rehash/retry or
  safe failure behavior.
- Concurrency tests run two staging attempts for the same stable source/digest and prove exactly one canonical
  default source session/batch is created; the losing attempt observes the winner's state.
- Structured and agent-extracted import tests prove one batch/source association and no duplicate batch by default.
- Bootstrap tests cover staged and partially committed source sessions, preserving reviewable/committable staging
  rows and source-session/batch references; older bundles without M18 fields still restore.
- Provenance tests prove every committed candidate traces to the correct source session while existing
  message/event-derived `Provenance.session` values remain byte/semantically unchanged.
- Evidence-link tests cover same-batch observation/interaction resolution, persisted-id references, wrong-person
  observations, interactions that omit the trait subject, missing/unaccepted evidence, lifecycle edge cases, stable
  ordering, and sensitivity filtering.
- Source list/show tests contain only bounded metadata/ids and never path/body sentinels.
- Sync/bootstrap/export tests explicitly account for the new durable state according to the chosen replication
  policy.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- Idempotency is source-level first; semantic record consolidation is intentionally left to M19.
- A source digest and its extracted candidates always come from one verified stable snapshot/pass; path reopen races
  are detected and discarded before staging.
- Duplicate claiming plus staging publication is atomic; check-then-insert races are not permitted.
- Existing per-message/event `Provenance.session` semantics are preserved; source-session traceability uses a
  separate durable association.
- Full bootstrap preserves incomplete staging needed by durable source-session references.
- Trait evidence is grounded only in observations/interactions involving the same person in M18.
- Automatic source rollback and automatic confidence recomputation remain deferred until real usage demonstrates
  safe semantics.
