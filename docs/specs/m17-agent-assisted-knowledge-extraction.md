# M17 — Agent-assisted knowledge extraction

Status: Planned. See [docs/roadmap.md](../roadmap.md#m17--agent-assisted-knowledge-extraction).

## Motivation

Structured exports are only one source of durable people context. Meeting transcripts, conversation transcripts,
interview notes, call notes, and other unstructured text often contain richer information about roles, preferences,
communication style, relationships, and observed behavior.

The project should not embed an LLM runtime to interpret that text. Agents already provide the semantic reasoning
layer and can read local files or attachments in their own environment. People Context should instead give agents a
strict, reviewable candidate vocabulary rich enough to represent what they distill, then reuse the existing
stage → review → commit gate.

M17 therefore expands agent-extracted candidates and documents a safe extraction workflow. The source text remains
outside the database; only distilled candidates enter staging.

## Scope

In scope:

- add strict staged candidate types for `observation`, `trait`, and `relationship`;
- commit those accepted candidate types through the existing record/relationship write use cases and atomic
  audit/changelog seam;
- require stronger evidence metadata for inferred traits than direct `record_trait` writes require;
- expose strict candidate staging through the CLI for agents that do not use MCP;
- add bounded resource contracts for the new CLI surface and every MCP request that uses a newly introduced M17
  candidate type, from the first PR that exposes those types;
- extend the packaged agent usage skill with an unstructured-source extraction workflow;
- preserve source text outside People Context and never add an internal model/API dependency.

Non-goals:

- parsing arbitrary prose deterministically inside People Context;
- running or selecting an LLM, storing API keys, or making model/network calls;
- automatically committing extracted knowledge;
- storing transcript/message bodies, verbatim evidence passages, embeddings of raw sources, or attachments;
- psychiatric diagnosis, demographic inference, or speculative sensitive-trait inference;
- extracting `sensitive`/`restricted` relationship edges before the durable relationship model/read surfaces have an
  enforceable sensitivity/disclosure contract;
- automatic trait confidence recomputation or cross-source consolidation (M19);
- source receipts/idempotency/evidence edges (M18);
- silently narrowing the released legacy-only MCP `stage_candidates` contract merely to add M17 candidate types.

## Epistemic model

Agents must distinguish three levels of knowledge rather than flattening all text into facts:

1. **fact** — an explicit durable assertion, e.g. “I joined Acme in January”;
2. **observation** — something that occurred in this source/context, e.g. “asked repeatedly for quantitative
   evidence before agreeing”;
3. **trait** — a generalized, subjective characteristic inferred from evidence, e.g. “responds better to proposals
   supported by quantitative evidence”.

The farther a candidate moves from explicit assertion toward inference, the stronger the evidence/provenance
requirements become. One observed behavior must not silently become a high-confidence personality claim.

Relationship statements are also first-class knowledge rather than generic facts: “Sarah manages Bob”, “Alice is
John's sister”, or “Mohsen reports to Quinn” should use the existing relationship normalization and graph semantics.
Known vocabulary gains category/direction/inverse behavior; legal unknown relationship types remain supported as
uncategorized edges under the existing write contract.

## Candidate vocabulary

Extend the strict discriminated union currently containing `person`, `interaction`, `affiliation`, and `fact`.

### Observation candidate

Conceptual shape:

```json
{
  "type": "observation",
  "person_ref": "alice",
  "text": "Asked for concrete metrics before agreeing to the proposal",
  "observed_at": "2026-08-24T10:00:00Z",
  "sensitivity": "personal"
}
```

`observed_at` may be absent only when the source does not establish an event time; commit then follows the existing
`RecordObservation` clock behavior. Agents should prefer source-established timestamps when available.

### Trait candidate

Conceptual shape:

```json
{
  "type": "trait",
  "person_ref": "alice",
  "category": "communication_style",
  "value": "Responds better to proposals supported by quantitative evidence",
  "evidence_note": "Derived from the 24 Aug planning meeting: repeatedly requested measurable evidence before agreeing.",
  "confidence": 0.65,
  "sensitivity": "personal"
}
```

For **staged agent-extracted traits**, both `evidence_note` and `confidence` are required even though direct
`RecordTraitInput` permits defaults. This is intentional boundary validation: inference imported from unstructured
material must never default to certainty or become evidence-free.

`evidence_note` is a concise distilled explanation, not a transcript excerpt. Documentation should discourage
verbatim quotations and long copied passages.

Allowed categories remain the existing `TraitCategory` values; M17 does not invent a second trait taxonomy.

### Relationship candidate

Conceptual shape:

```json
{
  "type": "relationship",
  "from_ref": "sarah",
  "to_ref": "bob",
  "relationship_type": "manager",
  "confidence": 1.0
}
```

Both refs are batch-local person refs. Staging resolves/matches person candidates as it already does, and commit
uses the existing M7 relationship contract through `SetRelationship`:

- normalize free-form type text to the existing stable snake-case representation;
- resolve registered vocabulary/synonyms and apply inverse/symmetric endpoint semantics when known;
- preserve a normalized unknown but syntactically valid type as a legal `uncategorized` relationship;
- reject only values that are blank or normalize to no word characters under the existing validation rule.

M17 must **not** require a relationship type to be pre-registered in the canonical vocabulary. Doing so would
narrow the existing `SetRelationship` contract and make currently legal uncategorized edges impossible to import.
The example `manager` therefore remains legal even if it does not resolve to a seeded synonym; callers that want
canonical `manages`/`reports_to` semantics should use a registered spelling/synonym.

Relationship extraction has one additional disclosure restriction. The current durable `Relationship` model and
ordinary graph/context reads do **not** carry an enforceable sensitivity field. M17 therefore treats every staged
relationship candidate as an **ordinary-disclosure (`public`/`personal`) edge**. If the source relationship would
need `sensitive` or `restricted` treatment under the existing disclosure policy, the agent must not create a
relationship candidate for it. M17 intentionally does not add a candidate-only `sensitivity` field that would be
silently discarded at commit and falsely imply protection the durable graph cannot enforce. Because candidate models
are strict, an attempted extra relationship `sensitivity` field fails rather than being accepted and ignored.

Adding elevated relationship storage/disclosure later requires a separate durable schema/write/read compatibility
design; it is not smuggled into agent extraction. Until then, explicit sensitive relationship statements remain
outside the relationship graph rather than being downgraded to ordinary-disclosure data.

If the current relationship write contract has no confidence field, omit it from the durable write rather than
changing relationship semantics solely for extraction; the staged schema may omit confidence too. Implementation
must prefer the existing domain contract over speculative schema expansion.

## Identity and dependency behavior

Agents should create a person candidate for every external participant they need to reference, even when that
person probably already exists. Candidate staging/matching then records the existing-person match and dependent
candidates continue to reference one batch-local person candidate id.

Do not add a second “existing person id or candidate ref” dependency language. One batch-local dependency model is
simpler and keeps review/commit behavior uniform across structured and agent-extracted imports.

An ambiguous identity must remain reviewable/unresolved. The agent must not create a new person merely to bypass an
ambiguous existing match.

## Commit behavior

Extend `CommitImport` using injected existing write use cases, not direct repositories:

- observation → `RecordObservation`;
- trait → `RecordTrait`;
- relationship → `SetRelationship`;
- existing person/affiliation/fact/interaction behavior remains unchanged.

Commit ordering must satisfy dependencies: people first, then person-scoped candidates/relationships/interactions
once all referenced people resolve. Accepted candidates with unresolved dependencies remain in `unresolved_ids`.

Every durable write continues through the normal atomic audit/changelog seam. M17 does not add a privileged import
write path.

## Agent-facing CLI candidate staging

Add a machine-oriented strict-candidate entry point under the M16 import group:

```text
pctx import stage-candidates --source SOURCE --input PATH [--json]
pctx import stage-candidates --source SOURCE --input - [--json]
```

The input is **candidate JSON**, not raw meeting text. `-` means read candidate JSON from stdin. The JSON validates
against the same candidate vocabulary used by MCP staging; unknown fields/types fail closed.

### Resource bounds

The new extraction surface is bounded from its first release. Binding limits are:

- at most **500 candidates** in any MCP `stage_candidates` request that contains at least one M17 candidate type;
- the normalized MCP `source` label is at most **128 characters** for any request containing an M17 candidate type;
- newly introduced observation `text` is capped at **4 KiB**;
- newly introduced trait `value` and `evidence_note` are each capped at **2 KiB**;
- newly introduced relationship type and batch-local reference strings are each capped at **256 characters**.

These MCP limits belong to the first implementation PR that exposes M17 candidate types. They are checked before
any staging row is written, and rejected values are never echoed in diagnostics. `StageCandidates` currently stores
its normalized `source` in every staged row and later provenance/audit paths, so bounding the M17 source label is a
privacy as well as a resource invariant: a caller must not be able to use `source` as a transcript-sized side
channel.

The later M17 CLI surface adds these additional process-boundary limits:

- at most **1 MiB** of UTF-8 JSON input before decoding/parsing;
- at most **500 candidates** per CLI staging request;
- no individual string value accepted by this CLI may exceed **8 KiB**;
- the CLI `--source` label is capped at the same **128 characters**.

Reject CLI limit violations before durable staging and report only bounded diagnostics; never echo the rejected
payload. Where practical, the byte limit is enforced while reading rather than after allocating an unbounded input
buffer. These are safety/resource limits, not suggested target sizes; normal distilled candidates should be far
smaller.

The same new-field limits apply whether an M17 candidate arrives through MCP or CLI. A legacy-only MCP batch
containing only the four pre-M17 candidate types retains its released accepted-shape behavior, including its existing
source-label behavior; M17 does not retroactively impose new count/string/source caps on that old surface. If the
project later wants global caps for legacy MCP staging too, treat that as an explicit compatibility/security
hardening decision rather than smuggling it into this additive candidate milestone.

This split is deliberate: M17.1 makes the newly accepted MCP forms safe at first exposure, while M17.2 adds the
extra byte/process bounds required by a file/stdin CLI adapter.

The command stages only. Review and commit continue through the M16 commands:

```text
pctx import review BATCH_ID --json
pctx import commit BATCH_ID --accept ... --json
```

Human agents using MCP may continue to call `stage_candidates` directly. The CLI exists so an agent with only
filesystem/process access can use the same application workflow without scraping human output.

## Agent extraction workflow

Extend the packaged usage skill with a workflow conceptually equivalent to:

1. read the source material using the agent's own file/attachment capability;
2. identify participants and distinguish explicit claims, observations, inferred traits, relationships, and
   interactions;
3. keep raw source text outside People Context;
4. create strict candidates with concise distilled values/evidence notes;
5. stage candidates only;
6. present/retrieve the review batch;
7. commit only after explicit user acceptance.

Guidance should explicitly discourage:

- speculative health, political, religious, sexual, demographic, or similarly sensitive inference;
- psychiatric/personality diagnosis;
- gossip promoted to fact;
- temporary emotion promoted to temperament;
- unsupported relationship guesses;
- **any sensitive/restricted relationship edge**, even when explicitly stated, because the current relationship
  store/read contract cannot preserve elevated disclosure;
- a single observation promoted to a high-confidence trait;
- copying raw transcript passages into evidence notes.

Sensitive information explicitly stated in source material may still be represented through candidate types whose
durable records actually enforce the existing sensitivity/provenance policy (for example facts, observations,
traits, or interactions). This exception does **not** apply to relationship candidates in M17: an elevated
relationship is omitted from the graph until relationship sensitivity exists as a durable contract. The agent should
distinguish extraction from inference rather than downgrading sensitive content merely because it was stated
explicitly.

## Migration needs

No schema migration is required if observation/trait/relationship candidates map entirely onto existing durable
entities. Candidate staging remains JSON-backed and strict-model validated.

## CLI / MCP surface changes

- MCP `stage_candidates` accepts three additive, bounded candidate types;
- from M17.1, requests using an M17 candidate type are bounded to 500 total candidates, a normalized 128-character
  source label, and the new-type field limits, while legacy-only MCP batches retain their pre-M17 accepted shape;
- `review_import` / `commit_import` response envelopes remain unchanged;
- M17.2 adds bounded CLI `pctx import stage-candidates ...` over the same application use case;
- no new raw-text import tool and no model execution tool.

## Security and privacy

- Raw unstructured source material is never persisted by People Context.
- Candidate JSON itself contains distilled personal information and must be treated as sensitive local data.
- Count/source/string limits prevent the new MCP extraction forms from becoming an accidental transcript archive or
  amplification path across hundreds of staging/provenance rows.
- Byte/count/string limits prevent the new agent-facing CLI surface from becoming an accidental transcript archive
  or unbounded-memory sink.
- Trait evidence notes must be concise derivations, not hidden transcript archives.
- Relationship candidates are ordinary-disclosure only in M17; sensitive/restricted relationships are not staged
  because the durable relationship model cannot enforce those disclosure levels.
- Agents never bypass the review gate merely because they performed the extraction.
- The CLI stdin path validates bounded JSON before staging and never evaluates code or invokes a shell.
- Candidate writes retain the sensitivity/provenance behavior their durable target actually supports; M17 never
  claims an elevated relationship policy that the graph schema/read surfaces do not implement.

## Testing strategy

- Strict-model tests cover valid/invalid observation, trait, and relationship candidates and reject extra fields.
- Relationship tests cover seeded/synonym canonicalization **and** a syntactically valid unknown type remaining a
  legal uncategorized edge; blank/non-word relationship types fail exactly as `SetRelationship` does. A relationship
  candidate carrying an unsupported `sensitivity` field fails strict validation rather than being silently ignored.
- Trait candidate tests require explicit confidence and non-blank evidence note.
- M17.1 boundary tests pin every new field limit, the conditional 500-candidate MCP cap, and the conditional
  128-character normalized MCP source-label cap; rejection occurs before staging and does not echo the source.
- Staging tests verify person-ref rewriting/matching for each new dependent type.
- Commit tests cover known/new/matched people, unresolved refs, relationship canonicalization/uncategorized behavior,
  and all new writes flowing through existing audited use cases.
- Regression tests pin all four existing candidate types and response envelopes unchanged, including a legacy-only
  MCP staging fixture proving M17 did not retroactively apply the new count/string/source limits to that contract.
- M17.2 CLI tests cover file/stdin candidate JSON, malformed JSON, unknown candidate types, >1 MiB rejection, >500
  candidates, oversized generic/new-type strings/source label, `--json`, and stdout purity.
- Skill/workflow tests or scripted transcript fixtures prove stage-only behavior, explicit commit approval, and that
  explicitly stated sensitive/restricted relationships do not produce relationship candidates.
- Raw-source sentinels must be absent from staged candidates except for deliberately distilled test values.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- People Context remains model-agnostic; semantic extraction belongs to the calling agent.
- Observation and trait candidates preserve the existing fact/observation/trait epistemic distinction.
- Relationship candidates preserve legal uncategorized relationship types instead of narrowing the existing write
  contract to registered vocabulary only.
- Relationship extraction is ordinary-disclosure only until the durable relationship model/read path grows an
  enforceable sensitivity contract; M17 does not silently downgrade elevated edges.
- The first MCP release of M17 candidate types already carries count, source-label, and new-field bounds; the CLI
  adds byte/process bounds in the following PR.
- Legacy-only MCP candidate behavior is not silently narrowed.
- Relationship candidates are included now because unstructured conversations routinely reveal graph edges and the
  relationship subsystem already exists.
- Source receipts, duplicate-source detection, durable evidence links, and consolidation are intentionally deferred
  to M18/M19 rather than overloading the candidate-vocabulary change.
