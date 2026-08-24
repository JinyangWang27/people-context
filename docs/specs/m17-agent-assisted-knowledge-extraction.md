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
- add bounded resource contracts for the new CLI surface and newly accepted M17 candidate forms;
- extend the packaged agent usage skill with an unstructured-source extraction workflow;
- preserve source text outside People Context and never add an internal model/API dependency.

Non-goals:

- parsing arbitrary prose deterministically inside People Context;
- running or selecting an LLM, storing API keys, or making model/network calls;
- automatically committing extracted knowledge;
- storing transcript/message bodies, verbatim evidence passages, embeddings of raw sources, or attachments;
- psychiatric diagnosis, demographic inference, or speculative sensitive-trait inference;
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

The new CLI surface is bounded from its first release. Binding limits are:

- at most **1 MiB** of UTF-8 JSON input before decoding/parsing;
- at most **500 candidates** per staging request;
- no individual string value accepted by this CLI may exceed **8 KiB**;
- newly introduced observation `text` is capped at **4 KiB**;
- newly introduced trait `value` and `evidence_note` are each capped at **2 KiB**;
- newly introduced relationship type and batch-local reference strings are each capped at **256 characters**;
- the CLI `--source` label is capped at **128 characters**.

Reject limit violations before durable staging and report only bounded diagnostics; never echo the rejected payload.
Where practical, the byte limit is enforced while reading rather than after allocating an unbounded input buffer.
These are safety/resource limits, not suggested target sizes; normal distilled candidates should be far smaller.

The same field limits are part of the **new M17 candidate models** accepted through MCP. Any MCP `stage_candidates`
request containing at least one M17 candidate type is also capped at 500 total candidates so the newly introduced
extraction path is bounded. A legacy-only MCP batch containing only the four pre-M17 candidate types retains its
released accepted-shape behavior; M17 does not retroactively impose a new batch/string cap on that old surface.
If the project later wants global caps for legacy MCP staging too, treat that as an explicit compatibility/security
hardening decision rather than smuggling it into this additive candidate milestone.

This split is deliberate: the new CLI can be safe-by-construction, and all newly accepted M17 forms are bounded,
without silently narrowing pre-existing integrations.

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
- a single observation promoted to a high-confidence trait;
- copying raw transcript passages into evidence notes.

Sensitive information explicitly stated in source material may still be represented according to existing
sensitivity/provenance policy; the agent should distinguish extraction from inference.

## Migration needs

No schema migration is required if observation/trait/relationship candidates map entirely onto existing durable
entities. Candidate staging remains JSON-backed and strict-model validated.

## CLI / MCP surface changes

- MCP `stage_candidates` accepts three additive, bounded candidate types;
- requests that use an M17 candidate type are bounded to 500 total candidates, while legacy-only MCP batches retain
  their pre-M17 accepted-shape behavior;
- `review_import` / `commit_import` response envelopes remain unchanged;
- new bounded CLI `pctx import stage-candidates ...` composes the existing staging use case;
- no new raw-text import tool and no model execution tool.

## Security and privacy

- Raw unstructured source material is never persisted by People Context.
- Candidate JSON itself contains distilled personal information and must be treated as sensitive local data.
- Byte/count/string limits prevent the new agent-facing surface from becoming an accidental transcript archive or
  unbounded-memory sink.
- Trait evidence notes must be concise derivations, not hidden transcript archives.
- Agents never bypass the review gate merely because they performed the extraction.
- The CLI stdin path validates bounded JSON before staging and never evaluates code or invokes a shell.
- All candidate writes retain existing sensitivity, provenance, audit, and changelog behavior.

## Testing strategy

- Strict-model tests cover valid/invalid observation, trait, and relationship candidates and reject extra fields.
- Relationship tests cover seeded/synonym canonicalization **and** a syntactically valid unknown type remaining a
  legal uncategorized edge; blank/non-word relationship types fail exactly as `SetRelationship` does.
- Trait candidate tests require explicit confidence and non-blank evidence note.
- Boundary tests pin every new M17 string limit and the 500-candidate cap for MCP requests using M17 candidates.
- Staging tests verify person-ref rewriting/matching for each new dependent type.
- Commit tests cover known/new/matched people, unresolved refs, relationship canonicalization/uncategorized behavior,
  and all new writes flowing through existing audited use cases.
- Regression tests pin all four existing candidate types and response envelopes unchanged, including a legacy-only
  MCP staging fixture proving M17 did not retroactively apply the new count/string limits to that contract.
- CLI tests cover file/stdin candidate JSON, malformed JSON, unknown candidate types, >1 MiB rejection, >500
  candidates, oversized generic/new-type strings, `--json`, and stdout purity.
- Skill/workflow tests or scripted transcript fixtures prove stage-only behavior and explicit commit approval.
- Raw-source sentinels must be absent from staged candidates except for deliberately distilled test values.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- People Context remains model-agnostic; semantic extraction belongs to the calling agent.
- Observation and trait candidates preserve the existing fact/observation/trait epistemic distinction.
- Relationship candidates preserve legal uncategorized relationship types instead of narrowing the existing write
  contract to registered vocabulary only.
- New CLI/M17 candidate forms are resource-bounded; legacy-only MCP candidate behavior is not silently narrowed.
- Relationship candidates are included now because unstructured conversations routinely reveal graph edges and the
  relationship subsystem already exists.
- Source receipts, duplicate-source detection, durable evidence links, and consolidation are intentionally deferred
  to M18/M19 rather than overloading the candidate-vocabulary change.
