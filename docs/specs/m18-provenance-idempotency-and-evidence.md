# M18 — Provenance, idempotency, and evidence

Status: Planned. See [docs/roadmap.md](../roadmap.md#m18--provenance-idempotency--evidence).

## Motivation

Once humans and agents can routinely import structured exports and distill unstructured material, duplicate
processing and provenance become operational concerns. Re-reading the same meeting transcript should not silently
create a second copy of the same observations, and a derived trait should be explainable in terms of the evidence
that motivated it rather than surviving as a free-floating conclusion.

M18 adds a minimal source/session layer that records *that* material was processed without storing the material
itself, makes repeat ingestion detectable, records which durable entity each committed candidate produced, and lets
inferred traits point to durable evidence records. The goal is traceability and safe idempotency, not document
management.

## Scope

In scope:

- first-class local source/ingestion receipts containing bounded metadata and a content digest, never raw source;
- hash/extraction consistency so a receipt digest always describes the same stable source snapshot that produced
  the staged candidates;
- extraction-configuration fingerprints so idempotency distinguishes the same bytes parsed under materially
  different options/self-identity inputs;
- atomic association of a staged import batch with one source session;
- a durable candidate commit mapping that is also the canonical record-to-source-session association and can
  represent a merge-retired candidate whose durable relationship no longer has a surviving edge;
- one logical transaction id across every audited/changelogged effect produced by one M18-tracked import commit;
- merge integration that retargets candidate mappings to surviving people/relationships instead of leaving inactive
  or deleted entity ids;
- duplicate-source detection before creating another staging batch plus an explicit non-default reprocessing escape
  hatch;
- versioned bootstrap continuity for source sessions, **all** durable committed-candidate mappings, and the staging
  rows required only for incomplete batches;
- M18-aware bootstrap baseline-empty checks for every new mutable source/mapping/evidence table, regardless of which
  supported bundle version is being restored;
- hard-forget integration for new durable provenance/evidence relations and structurally linked retained staging so
  erased records cannot remain addressable or reviewable through import state;
- privacy-preserving terminal source claims that retain only the non-restageable claim key/status after all derived
  personal state has been forgotten;
- bounded, keyset-paginated CLI inspection of source sessions and their derived records/batches;
- durable evidence links from traits to observations/interactions that support the same person;
- explicit bounded batch-local evidence references so one staged trait can address observation/interaction
  candidates before People Context allocates canonical candidate ids;
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
- bounded `source_kind`, which is a non-personal **machine category** such as `linkedin`, `ics`, or
  `meeting_transcript`, not a human description such as `interview_with_alice`;
- optional bounded user/caller label for human inspection;
- SHA-256 digest of the exact stable source bytes when a source artifact exists;
- an extraction fingerprint describing the extraction-affecting configuration without persisting raw options/self
  identities;
- optional externally supplied stable source id when the caller has one;
- created/processed timestamp;
- associated staging batch id when applicable;
- status sufficient to distinguish staged/partially committed/committed/redacted without duplicating candidate-row
  truth.

New source-session metadata is bounded at the process boundary. At minimum:

- `source_kind`: non-blank, at most **128 characters**, restricted to a conservative machine-identifier alphabet
  such as ASCII letters, digits, `.`, `_`, `-`, and `/`; it describes a source class/adapter, not a person/source
  title;
- optional human label and external source id: at most **256 characters** each;
- SHA-256 content digest and extraction fingerprint: exactly **64 lowercase hexadecimal characters** when present;
- optional extraction-contract revision identifier: at most **64 ASCII characters**, restricted to a conservative
  identifier alphabet such as letters, digits, `.`, `_`, and `-`.

Normalize only fields whose semantics explicitly define normalization; do not silently case-fold opaque external
identifiers. Rejected metadata is never echoed with its original value. Human descriptions belong in the optional
label, not `source_kind`, so a terminal redacted claim can retain its canonical claim key without retaining a name or
other caller-authored identifying description.

Do **not** store:

- source body/content;
- copied transcript excerpts;
- attachments;
- absolute filesystem paths by default;
- raw self-address/name/sender configuration merely to make an idempotency key;
- model prompts/responses;
- credentials or external service tokens.

A digest or extraction fingerprint is an idempotency key, not a privacy transform; documentation must still treat
both as metadata about personal source material.

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

M18 retains M16's file/candidate process budgets while adding the stronger snapshot guarantee. Stable-snapshot
verification must never bypass or double the bounded read budget by repeatedly loading an oversized source into
memory.

### Extraction fingerprint

The same bytes can legitimately produce different candidates when extraction-affecting inputs change. WhatsApp is
the obvious example: changing `self_sender` changes which sender is omitted, and the existing import composition also
passes self handles/names into extractors.

For structured imports, derive a deterministic `extraction_fingerprint` from a canonical representation of only the
inputs that can affect extraction for that source. At minimum this includes:

- the normalized explicit `self_sender` where that source uses it;
- the normalized self-address/self-name identity snapshot actually supplied to the extractor where relevant;
- a stable per-source extraction-contract revision controlled by People Context, so an intentional future change in
  parsing semantics can opt into a new claim identity rather than silently reusing an old batch.

Persist only the fingerprint (and the bounded contract-revision identifier if useful for inspection), not the raw
self identity values used to derive it. Canonicalization and ordering must be deterministic.

The default duplicate claim identity is therefore:

```text
(source_kind, content_digest, extraction_fingerprint)
```

not merely `(source_kind, content_digest)`. Retrying the same WhatsApp bytes with a corrected `self_sender` must be
able to create the correct canonical batch without pretending it is the same extraction as the earlier attempt.

After a stable source snapshot and extraction fingerprint exist, the default claim, source-session insertion,
staging-batch creation, source-session/batch association, and candidate-row staging become visible atomically. Two
concurrent processes using the same claim identity must not both create default source sessions/batches.

Use a database uniqueness mechanism plus one transaction (for example `BEGIN IMMEDIATE` around the claim/stage
write) rather than a check-then-insert race. If another process wins the canonical claim, the loser returns/reports
the already-existing source session and batch state without creating any new durable row.

M18 also **requires** an explicit non-default file-import reprocessing control, conceptually:

```text
pctx import stage SOURCE PATH --force
```

`--force` creates a distinct processing session while retaining the same content digest/extraction fingerprint and
never weakens the canonical default uniqueness rule. It is for intentional reprocessing of exactly the same claim,
not a workaround for incorrect claim identity.

### Agent-extracted sources

`pctx import stage-candidates` / MCP staging may accept optional source-session metadata including a digest computed
by the calling agent over the source artifact. When no digest is supplied, the workflow remains valid but cannot
claim source-level idempotency.

People Context must never hash raw text that it has not been given. The agent remains responsible for reading the
source and may provide the exact-byte digest when its environment can do so deterministically. A caller-supplied
digest is provenance metadata from that caller; People Context must not imply it independently verified source
bytes that were never provided.

If an agent workflow supplies an extraction/configuration fingerprint, its semantics must be explicit and bounded;
otherwise omit it rather than inventing unverifiable configuration state. The structured-import fingerprint above
is authoritative only for People Context-controlled extraction.

### Candidate-level duplicates

M18 does not attempt semantic candidate deduplication. Two different sources may legitimately assert the same fact
or observation. Source-level idempotency prevents accidental reprocessing of one extraction claim; knowledge
consolidation is a separate M19 concern.

## Candidate commit mapping and provenance

A source session is an additional durable ingestion anchor; it does **not** replace or repurpose existing
`Provenance.session` semantics.

Existing import behavior that stores source-local message/event identifiers in `Provenance.session` is a stable
contract and remains unchanged. In particular, email message ids, calendar event ids, and any existing candidate
`message_id`-derived session values retain their current meaning.

M18.1 adds the smallest durable commit-outcome mapping needed to preserve provenance and dependency resolution across
separate commit invocations. Conceptually each committed staging candidate records:

```text
candidate_id
batch_id
source_session_id
disposition          # entity | merged_away
entity_type
entity_id | null
```

`entity_id` is required for the normal `entity` disposition. `merged_away` is a narrow terminal lifecycle outcome
used only when a later person merge intentionally removes a committed relationship as a self-loop and no equivalent
durable relationship survives; it carries no dangling relationship id. Exact table/port naming is implementation-
time.

The mapping has these invariants:

- one newly committed candidate resolves deterministically to the durable primary entity returned/affected by its
  normal write use case;
- the mapping and the candidate's transition to `committed` are written in the same unit of work as that durable
  mutation, so a committed status can never become visible without its output mapping;
- one `CommitImport.execute` invocation uses **one non-empty logical `transaction_id`** for every audited/changelogged
  durable effect it produces: all child entity writes, candidate commit mappings, and any source-session status
  update. Multiple accepted candidates committed by that invocation share the same transaction id; unresolved rows
  produce no durable effect. Propagation is an internal application seam and does not require exposing a new public
  MCP argument on ordinary write tools;
- matched/existing entities are legal mapping targets when the existing write contract updates/reuses them;
- retrying an already committed candidate uses the stored mapping/outcome rather than name/text heuristics;
- a terminal `merged_away` mapping is still treated as already committed and must not recreate its removed self-loop
  relationship on retry;
- the mapping remains durable even if completed staging rows are eventually cleaned up;
- `source_session_id` makes this same relation the canonical record-to-source-session association used by source
  inspection, avoiding a second parallel provenance table;
- hard-forgetting a durable mapped entity removes every mapping targeting that entity in the **same forget
  transaction**, and the forget preview/counts include those mapping rows;
- mapping audit/changelog history selected by a record- or person-scope hard forget is redacted with the same
  guarantees as the forgotten entity, so a mapping id/payload cannot remain a replayable provenance side channel.

### Merge behavior

`merge_people` already reparents person-owned records, removes relationship self-loops, and deduplicates overlapping
relationships under one logical merge transaction. M18.1 extends that same transaction to candidate commit mappings:

- a mapping to the duplicate person is retargeted to the surviving primary person;
- mappings to facts/observations/traits/reminders/affiliations whose entity ids survive reparenting keep those entity
  ids;
- a mapping to a relationship that is removed as a duplicate is retargeted to the relationship keeper chosen by
  the existing merge policy; multiple candidates may therefore legitimately map to the same surviving edge;
- a mapping to a relationship removed as a merge-created self-loop is changed to terminal `merged_away` with
  `entity_type="relationship"` and `entity_id=null` rather than dangling, being deleted, or being pointed at an
  unrelated entity;
- mapping retarget/terminal changes are emitted through the normal lifecycle audit/changelog seam using the **same
  `transaction_id` as the person/record/relationship merge effects**.

Bootstrap validation and source inspection understand both mapping dispositions. An `entity` mapping must reference
a durable entity present in the snapshot/store. A `merged_away` mapping must have no entity id and is displayed only
as a terminal candidate outcome, not as a live record reference.

### Hard forget and retained staging

For person-scope forget, durable mapping cleanup follows the actual entities the existing forget operation erases:
mappings to the person and to records/relationships/orphan interactions that are hard-deleted are removed; a mapping
to a shared interaction that remains after removing only that person's participation remains valid because its
target entity still exists. Record-scope forget removes mappings to the selected record. Source-session rows are not
automatically cascaded merely because one derived entity is forgotten; they may still provenance other retained
entities, but source inspection must no longer expose any forgotten mapping.

Hard forget must also remove **operational retained staging content that is structurally linked to the erased
entity**, because incomplete batches can otherwise keep the forgotten name/text reviewable even after the durable
record is gone. Do not free-text scan or guess by name. Use the strict staged candidate reference structure:

- delete any retained staging row whose candidate id maps to an entity being erased;
- for person forget, also delete a pending/matched person candidate whose `matched_person_id` is the erased person;
- recursively delete retained dependent rows whose canonical typed references point at a removed candidate id,
  including person/end-point/participant/evidence candidate-id fields introduced by M17/M18;
- delete a retained trait candidate whose explicit durable `evidence_ids` contains a record being erased;
- continue dependency deletion to a fixed point so no remaining staged candidate contains a canonical reference to
  a removed candidate/entity.

Delete affected rows rather than persisting malformed/redacted candidate JSON. `import_staging` remains operational
state, so these staging-row deletions do not mint ordinary audit/changelog entries, but they occur inside the same
hard-forget transaction and their counts appear in preview/result metadata. Durable mapping/evidence-relation
changes remain part of the forget replay manifest/affected-entity set so peer replay can erase the corresponding
primary state.

If staging cleanup leaves an M18-tracked source with **no live mapped entity and no reviewable staging row**, retain
only the minimum non-restageable claim rather than its inspectable caller metadata. In the same forget transaction:

- set the source session to terminal `redacted`;
- clear the optional human label, external source id, batch association, and any other caller-authored optional
  inspection metadata not required by the canonical duplicate claim;
- retain only the internal source-session id, the canonical claim key `(source_kind, content_digest,
  extraction_fingerprint)`, and redacted status as durable claim identity; implementation-required timestamps may
  remain internally if the schema needs them, but redacted inspection does not expose them;
- redact prior audit payloads and covered changelog history that contain the cleared label/external id/batch or other
  removed caller metadata, with the same atomic privacy guarantee as the forgotten mapped entities.

`source_kind` remains safe to retain because M18 defines it as a non-personal machine category rather than a human
source title. A redacted claim must not expose its former label, external source id, batch id, timestamps, candidate
counts, or removed mappings through `source show`/`sources`. Duplicate detection must still recognize the canonical
claim and must not silently restage the forgotten source; explicit `--force` remains the intentional route to
reprocess it. If unrelated live mappings or reviewable rows remain, the source stays non-redacted and inspectable only
with those retained survivors.

Pre-M18 legacy batches that never had a source session are not guessed/backfilled from ambiguous audit history. New
M18-tracked batches must always satisfy the mapping invariant from the first release of source sessions.

The resulting provenance model is:

- `source` continues to describe the import/extraction surface;
- existing `Provenance.session` values keep their existing source-local semantics;
- every committed candidate in an M18-tracked batch has a durable candidate commit outcome mapping;
- source inspection and later same-batch dependencies traverse live `entity` mappings without storing the raw
  source, while terminal merge outcomes remain non-dangling history;
- hard forget removes provenance associations and structurally linked staging content for entities it actually
  erases, and a fully emptied source retains only a non-identifying non-restageable claim key/status rather than
  caller-authored source metadata.

## Bootstrap bundle versioning

The bootstrap sync bundle is intentionally strict: nested models forbid unknown fields and older readers cannot
ignore additions. M18 therefore **does not add fields to bundle version 1**.

Every M18 migration that creates a mutable table also extends the existing transactional baseline-empty check. On an
M18.1-capable installation, source-session and candidate-mapping tables must be empty before restoring **v1 or v2**.
After M18.3, the trait-evidence table is also required empty before restoring **v1, v2, or v3**. The incoming bundle
version never permits merging into unrelated local M18 state; tests explicitly cover a v1 restore refused because an
M18-only table contains a row.

### M18.1 — bundle version 2

M18.1 introduces `people-context-sync-bundle` **version 2**. The exporter emits v2 and the restorer accepts both the
released v1 shape and the new strict v2 shape.

V2 carries the new durable provenance state plus only the operational staging rows that are still needed:

- all source sessions/claim metadata, including minimal terminal redacted claims with cleared caller metadata;
- **all candidate commit mappings for every exported M18-tracked source session, including fully committed sessions
  whose staging rows have already been cleaned up**;
- staging rows only for staged or partially committed M18-tracked batches that must remain reviewable/committable.

The distinction is binding: commit mappings are durable primary provenance and are never restricted to incomplete
batches merely because incomplete staging is the only operational state that needs transfer. A fully committed
source restored from v2/v3 must retain the same candidate→entity associations so `source show` and future provenance
reads still identify its derived records.

Restore validates source-session → mapping references for every mapping. An `entity` disposition must reference a
durable entity present in the bundle; a terminal `merged_away` relationship mapping must have `entity_id=null` and
needs no live-entity reference. A terminal `redacted` source must satisfy the minimal-claim invariant and must not
carry cleared caller metadata or reviewable staging. Restore additionally validates source-session → batch →
staging/mapping references for incomplete batches and restores the whole document atomically. A partially committed
source must not arrive in a state where duplicate detection suppresses re-staging, `review_import` cannot find
pending candidates, or a committed candidate has lost the durable entity id needed by a later dependency. A
completed source must not arrive with its source session intact but its record associations missing.

V1 bundles remain valid inputs and simply contain no M18 source-session state. Existing v1 readers continue to reject
v2 as designed rather than accidentally accepting a document they do not understand.

M18.2 adds source-inspection/read surfaces over the same v2 state and does not change the bootstrap document shape.

### M18.3 — bundle version 3

M18.3 adds durable trait-evidence relations, which are additional primary state and therefore require another strict
bundle version. The exporter emits **version 3** after M18.3; the restorer accepts v1, v2, and v3. V3 adds the
evidence-relation collection to the v2 shape and validates it fail-closed; it inherits v2's requirement to carry all
candidate commit mappings, including mappings from completed source sessions and terminal merge outcomes, and the
same minimal terminal-redacted-source invariant.

Do not predeclare future fields in v2 merely to avoid a version increment: a bundle version means the reader fully
understands the semantics of every field it accepts.

Incremental peer replay of staging state remains out of scope; these requirements apply to the existing full-bundle
bootstrap/backup contract.

## Trait evidence links

Traits are inferred state and should be able to reference durable evidence. M18.3 adds a narrow evidence relation
from a trait to one or more existing observations/interactions.

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
- hard-forgetting a trait deletes its evidence-link rows, and hard-forgetting an observation/interaction deletes
  links that cite that erased evidence; those relation rows participate in preview counts, the forget replay
  manifest, and audit/changelog redaction in the same forget transaction;
- person-scope forget applies the same rule to every trait/evidence entity it actually hard-deletes, while links to
  a shared interaction that remains durable are not removed merely because one participant was forgotten;
- retrieval should be able to explain a trait with evidence ids and concise evidence metadata while respecting
  disclosure/sensitivity rules;
- `evidence_note` remains useful human-readable context and is not removed merely because evidence ids exist.

### Addressable same-batch evidence

A calling agent cannot know staging-row ids before `CandidateStager` allocates them, so M18.3 must not require callers
to predict or recover canonical candidate ids. Instead it extends the strict candidate vocabulary additively with a
small caller-addressable dependency language:

- observation and interaction candidates may include optional `evidence_ref`: a non-blank opaque batch-local string
  of at most **256 characters**;
- a trait candidate may include `evidence_refs`: up to **32** unique batch-local strings, each at most 256
  characters, referring to observation/interaction `evidence_ref` values in the same request;
- a trait candidate may separately include `evidence_ids`: up to **32** unique durable observation/interaction ids
  already present in the store;
- each caller-supplied `evidence_id` is exactly **26 Crockford-Base32 ULID characters** (case-insensitive input may be
  normalized to the canonical uppercase form); invalid alphabet/length values fail before staging and the rejected
  value is never echoed;
- the combined number of `evidence_refs` + `evidence_ids` on one trait is at most **32**.

`evidence_ref` values are exact opaque tokens after surrounding-whitespace rejection; do not case-fold or otherwise
normalize them into identity semantics. They must be unique among evidence-capable candidates in one batch. Duplicate
or unknown `evidence_refs`, a ref targeting an unsupported candidate type, blank refs, overlong refs, malformed
`evidence_ids`, or an over-budget trait fail strict validation before staging.

During staging, build the evidence-ref map before writing rows. Mirror the existing person-ref rewrite pattern:

1. allocate canonical staging candidate ids for all candidates;
2. map each caller `evidence_ref` to the allocated observation/interaction candidate id;
3. remove caller-local `evidence_ref` / `evidence_refs` from the persisted staged candidate representation;
4. persist the trait's rewritten canonical `evidence_candidate_ids` plus canonicalized durable `evidence_ids`.

This gives the caller one self-contained request format while keeping canonical candidate ids authoritative after
staging. It does not add an append-to-batch API or expose internal id allocation.

For same-batch agent extraction, commit resolves rewritten `evidence_candidate_ids` through the M18.1 candidate
commit mapping:

- evidence committed in an earlier partial commit resolves through its persisted candidate→entity mapping;
- evidence committed earlier in the current invocation creates its mapping before dependent trait resolution;
- explicit durable `evidence_ids` are loaded directly through the supported evidence read port;
- every resolved durable entity then passes the same type, active-state, and subject-ownership checks;
- if required accepted evidence is not yet resolvable, has no valid live-entity mapping, does not exist, or belongs
  to a different person, the trait remains unresolved rather than dropping the link or guessing.

A trait may omit durable evidence links entirely; M17's required concise `evidence_note` and explicit confidence
remain the staged inference boundary. M18 adds addressable durable grounding where evidence records exist rather than
retroactively making every M17 trait depend on a durable observation/interaction.

Do not allow a trait to cite another trait as evidence in M18; this keeps inference grounded in observed/interacted
material and avoids recursive belief chains.

## Source inspection

M18.2 exposes local inspection sufficient to answer “where did this come from?” without becoming a document
browser. Conceptual CLI:

```text
pctx sources [--limit N] [--cursor CURSOR] [--json]
pctx source show SOURCE_SESSION_ID [--json]
```

`pctx sources` is bounded at the read boundary:

- default `limit` is **50**, accepted range is **1..200**;
- order is deterministic newest-first by `(created_at DESC, id DESC)`;
- pagination is keyset/cursor based, not an unbounded read followed by slicing. The opaque cursor represents the last
  returned `(created_at, id)` key and must be bounded/validated before query execution;
- the SQLite reader applies the cursor predicate and `LIMIT limit + 1` itself, returns at most `limit` rows plus a
  `next_cursor` indication, and never materializes the full source-session table merely to render one page;
- stable machine JSON includes the applied limit and nullable `next_cursor`; human rendering uses the same bounded
  application result.

A non-redacted source detail may include:

- id, kind, optional label, digest/extraction fingerprint, timestamps/status;
- staging batch id;
- candidate counts/status summaries;
- committed candidate ids and live durable record ids/types from candidate mappings;
- a bounded terminal `merged_away` disposition for a committed relationship candidate whose edge disappeared during
  identity merge, without returning the removed edge id.

A terminal `redacted` source is deliberately narrower: list/show returns only its internal id, non-personal
`source_kind`, digest/extraction fingerprint claim key, and `redacted` status. It does not expose the cleared human
label, external source id, batch id, timestamps, candidate counts, mappings, or former optional metadata.

Inspection never returns raw source content or raw extraction self-identity configuration. Stable JSON is versioned
from first release if documented for agents. Human output may render concise provenance paths. Hard-forgotten mapping
rows and structurally linked staging rows are absent, so inspection/review cannot resurrect an erased record id or
its retained candidate content through provenance metadata.

## Migration needs

Expected additive migration(s):

- M18.1: source-session/receipt table with a concurrency-safe canonical claim over source kind + content digest +
  extraction fingerprint, batch association, and durable candidate commit-outcome mapping;
- M18.3: trait-evidence relation table with foreign keys/indexes appropriate to supported lifecycle behavior.

Use the next free migration number at implementation time. Every new durable write participates in the established
atomic audit/changelog seam unless the row is explicitly operational staging state under the documented bootstrap
policy. Source sessions, candidate commit mappings, and trait-evidence metadata that affect user-visible provenance
are replicable primary state. Incomplete staging remains operational state but is carried in full bootstrap bundles
so durable source/batch/dependency references cannot become dangling.

The same PR that creates a mutable M18 table adds it to the bootstrap baseline-empty check. This applies to every
supported incoming bundle version, including v1, because freshness is a property of the destination database rather
than of the document being restored.

The existing lifecycle paths are extended in the same PR that introduces each new durable relation. M18.1 integrates
candidate mappings with person merge, record/person hard-forget preview/deletion/replay-history redaction, typed
retained-staging cleanup, and terminal source-receipt metadata scrubbing. M18.3 applies the same hard-forget guarantees
to trait-evidence relation rows. New relation or receipt history must not survive merely because its entity id differs
from the record id being forgotten.

## CLI / MCP surface changes

Expected additive changes:

- M18.1 file staging reports duplicate-source state and source-session id and adds explicit `--force` reprocessing;
- M17 candidate staging may optionally accept bounded source-session metadata/digest;
- M18.2 adds bounded, cursor-paginated local source list/show inspection over candidate commit mappings;
- M18.3 additively accepts bounded `evidence_ref` on observation/interaction candidates and bounded, canonical-ULID
  `evidence_refs`/`evidence_ids` on trait candidates, rewriting batch-local refs to canonical candidate ids;
- trait/context representations may gain additive evidence metadata in M18.3;
- sync bundle emission advances to v2 in M18.1 and v3 in M18.3 while restore remains backward-compatible with
  prior supported versions;
- no raw-source retrieval tool.

Do not break existing import envelopes; add fields only where allowed by the M12 compatibility promise or introduce
new versioned JSON documents when a new machine surface is required. Existing provenance fields keep their prior
meaning. Existing interaction candidates remain valid without `evidence_ref`; the new field is optional and additive.

## Security and privacy

- Source receipts are metadata about personal material and must be treated as sensitive local state.
- `source_kind` is a machine category, not a place for names/source titles; human descriptions belong only in the
  optional label that hard forget can scrub.
- SHA-256 digests/extraction fingerprints are not anonymization and must not be presented as such.
- Digest/source attribution is accepted only after stable snapshot verification for file imports; a TOCTOU race
  must not attach digest A to candidates parsed from bytes B.
- Raw self identities/options used to derive an extraction fingerprint are not copied into the receipt merely for
  idempotency.
- Absolute source paths are not persisted by default because they can disclose usernames, organizations, project
  names, and machine layout.
- Source-session labels/ids and evidence refs/ids are explicitly bounded; none is a place to copy a source excerpt or
  transcript body.
- When forget removes the final live derived state for a source, caller-authored receipt metadata is cleared and its
  audit/covered changelog history redacted; list/show exposes only the non-restageable claim key/status.
- Evidence retrieval respects existing sensitivity/disclosure gates; a trait must not reveal restricted evidence to
  an ordinary MCP caller merely because the trait is visible.
- Subject validation prevents Alice's trait from exposing Bob-only observation metadata or an interaction in which
  Alice did not participate.
- Hard forget removes new provenance/evidence relations to erased entities, deletes structurally linked retained
  candidate rows, scrubs terminal receipt metadata, and redacts durable relation/receipt audit/changelog history
  atomically, preventing import metadata from becoming a post-erasure identifier/content leak.
- No raw source text enters logs, audit payloads, changelog payloads, errors, source-session rows, or temporary
  persistent copies created solely for hashing/extraction consistency.
- Duplicate detection happens locally without external lookups.

## Testing strategy

- Migration tests cover fresh and upgraded databases, FK integrity, indexes, and sync compatibility.
- Exact-byte digest tests cover deterministic hashing, same-claim duplicate detection, source-kind scoping, and
  intentional distinct forced sessions.
- Source-session metadata tests pin all length/digest/identifier bounds, the machine-category-only `source_kind`
  contract, and prove rejected values are not echoed.
- Extraction-fingerprint tests prove the same WhatsApp bytes with different effective `self_sender`/self identity
  configuration do not alias to the same canonical claim, while equivalent normalized configuration does.
- Snapshot-consistency tests prove byte-capable importers hash and parse the exact same immutable bytes.
- Concurrent-modification tests change a source between/during hash and extraction and prove no durable receipt or
  staging batch is created from mismatched bytes. Include the path-only `mbox` case and assert stable rehash/retry or
  safe failure behavior.
- Concurrency tests run two staging attempts for the same stable claim identity and prove exactly one canonical
  default source session/batch is created; the losing attempt observes the winner's state.
- Candidate commit mapping tests prove entity ids/outcomes are persisted atomically with durable writes/status
  transitions, already-committed retries resolve through the mapping, terminal merge outcomes do not recreate
  removed self-loop relationships, and mapping rows survive staging cleanup policy.
- Import-commit transaction tests accept multiple candidates and assert every entity, source-session status, and
  mapping changelog effect from one successful `CommitImport.execute` shares one non-empty transaction id; a phase
  failure rolls back all of them.
- Merge tests cover duplicate-person mapping retarget, unchanged ids for reparented records, relationship
  removed→keeper retarget, terminal self-loop disposition, source inspection, and the shared merge transaction id.
- Bundle tests pin strict v1/v2 compatibility at M18.1, v1/v2/v3 compatibility at M18.3, reject unknown fields per
  declared version, and prove each exporter emits only its current version.
- Bootstrap tests cover staged, partially committed, **and fully committed** source sessions. They preserve all
  durable candidate commit mappings for completed and incomplete sessions, carry staging rows only for incomplete
  batches, and prove `source show` after restore still reports derived records for a completed source whose staging
  rows were cleaned up. Baseline tests also prove any non-empty M18.1/M18.3 table rejects restore under the installed
  schema for **every** accepted incoming version, including a v1 bundle. Terminal-redacted bundle fixtures prove
  cleared caller metadata cannot reappear after restore.
- Provenance tests prove every committed M18-tracked candidate traces to the correct source session while existing
  message/event-derived `Provenance.session` values remain byte/semantically unchanged.
- Hard-forget tests cover both record and person scope: preview counts include relation and structurally linked
  staging rows; typed staging dependencies are deleted to a fixed point without free-text guessing; mappings/
  evidence links targeting entities actually erased are deleted in the same transaction; shared retained
  interactions keep valid durable mappings/links while any staging row that still references the forgotten candidate
  is removed; relation audit payloads are redacted; covered relation changelog operations/transactions are redacted;
  a source with no live mapping/reviewable staging is set `redacted`, clears label/external id/batch and other caller
  metadata, redacts their history, remains non-restageable, and returns only minimal claim-key/status metadata from
  source inspection afterward.
- Evidence-reference validation tests cover unique/duplicate/unknown/wrong-type/blank/overlong `evidence_ref`, exact
  canonical ULID validation/canonicalization for every `evidence_id`, the 32-reference combined trait budget,
  deterministic rewrite to canonical candidate ids, no rejected-value echo, and legacy interaction candidates without
  the optional ref remaining unchanged.
- Evidence-link tests cover evidence committed in an earlier partial commit and in the current invocation,
  explicit durable `evidence_ids`, persisted candidate mappings, wrong-person observations, interactions that omit
  the trait subject, missing/unaccepted evidence, lifecycle edge cases, stable ordering, and sensitivity filtering.
- Source list/show tests contain only bounded metadata/ids and never path/body/raw-self-configuration sentinels.
  Listing tests pin default `50`, range `1..200`, deterministic `(created_at DESC, id DESC)` order, opaque keyset
  cursor behavior, `next_cursor`, and a SQLite `LIMIT limit + 1` query so large stores are never fully materialized.
- Sync/bootstrap/export tests explicitly account for the new durable state according to the versioned policy.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- Idempotency is source/extraction-claim level first; semantic record consolidation is intentionally left to M19.
- A source digest and its extracted candidates always come from one verified stable snapshot/pass; path reopen races
  are detected and discarded before staging.
- Extraction-affecting configuration participates in claim identity through a deterministic fingerprint; raw self
  identity values are not stored merely to achieve this.
- Duplicate claiming plus staging publication is atomic; check-then-insert races are not permitted, and `--force`
  is the explicit escape hatch for intentional identical reprocessing.
- One import commit is one logical sync transaction: every audited/changelogged child entity, source-session status,
  and candidate mapping effect shares one transaction id and rolls back together.
- Candidate commit mapping is durable from M18.1 and doubles as the record→source-session provenance seam, allowing
  later partial commits to resolve already-committed dependencies without guessing. Person merge retargets it to
  surviving identities/edges; relationship self-loops with no survivor become explicit terminal `merged_away`
  outcomes rather than dangling references.
- Full bootstrap carries every durable candidate mapping, including completed source sessions and terminal merge
  outcomes; only incomplete staging rows are operationally scoped to staged/partially committed batches. Every new
  M18 mutable table participates in the destination baseline-empty check for all accepted bundle versions.
- New durable provenance/evidence relations participate in merge/forget lifecycle behavior as applicable from the
  PR that introduces them; hard forget also deletes structurally linked operational staging rows to a fixed point.
  Once no live derived/reviewable state remains, the source receipt is reduced to a non-restageable canonical claim
  key/status and caller-authored inspectable metadata/history is scrubbed.
- Source inspection is bounded at the storage read: default 50/max 200 keyset-paginated rows, never an unbounded
  source-session materialization followed by rendering-time truncation.
- Same-batch evidence uses bounded caller `evidence_ref` tokens rewritten to canonical candidate ids during staging;
  explicit durable evidence ids are canonical 26-character ULIDs, so neither field can become an unbounded raw-text
  side channel.
- Existing per-message/event `Provenance.session` semantics are preserved.
- Strict bootstrap additions advance document versions: v2 for M18.1 source/staging/commit-map state and v3 for
  M18.3 trait evidence; older supported versions remain readable.
- Trait evidence is grounded only in observations/interactions involving the same person in M18.
- Automatic source rollback and automatic confidence recomputation remain deferred until real usage demonstrates
  safe semantics.
